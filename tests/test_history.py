from core.self_improvement import history


def test_record_and_get_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_HISTORY_PATH", tmp_path / "history.json")

    entry_id = history.record({"problem": "volume tool crashes", "status": "success"})
    entry = history.get(entry_id)

    assert entry is not None
    assert entry["problem"] == "volume tool crashes"
    assert entry["status"] == "success"
    assert "timestamp" in entry


def test_get_unknown_id_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_HISTORY_PATH", tmp_path / "history.json")
    assert history.get("does-not-exist") is None


def test_update_status_changes_existing_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_HISTORY_PATH", tmp_path / "history.json")

    entry_id = history.record({"problem": "x", "status": "awaiting_approval"})
    updated = history.update_status(entry_id, "success", {"reason": "Approved by user."})

    assert updated is True
    entry = history.get(entry_id)
    assert entry["status"] == "success"
    assert entry["reason"] == "Approved by user."
    assert "resolved_at" in entry


def test_update_status_unknown_id_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_HISTORY_PATH", tmp_path / "history.json")
    assert history.update_status("nope", "success") is False


def test_recent_failed_solutions_excludes_success(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_HISTORY_PATH", tmp_path / "history.json")

    history.record({"problem_signature": "sig-a", "status": "success", "solution": "worked"})
    history.record({"problem_signature": "sig-a", "status": "rolled_back", "solution": "attempt 1"})
    history.record({"problem_signature": "sig-a", "status": "failed", "solution": "attempt 2"})
    history.record({"problem_signature": "sig-b", "status": "rolled_back", "solution": "unrelated"})

    failures = history.recent_failed_solutions("sig-a")

    assert len(failures) == 2
    assert {f["solution"] for f in failures} == {"attempt 1", "attempt 2"}


def test_corrupt_history_file_starts_fresh(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text("not valid json{{{", encoding="utf-8")
    monkeypatch.setattr(history, "_HISTORY_PATH", path)

    assert history.all_entries() == []
    entry_id = history.record({"problem": "x", "status": "success"})
    assert history.get(entry_id) is not None
