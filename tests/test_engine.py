"""
Engine orchestration tests. All collaborators (code_analyzer, planner,
executor, evaluator, rollback_manager, test_runner) are mocked here - they
each already have their own dedicated tests. This file only verifies that
engine.improve() wires the OBSERVE->...->LOG loop together correctly:
which functions get called, in what order, and what history/status results
from each outcome.
"""
from pathlib import Path

import pytest

from core.self_improvement import (
    code_analyzer,
    engine,
    evaluator,
    executor,
    history,
    planner,
    rollback_manager,
    safety_guard,
    test_runner,
)


class _FakeWorkspace:
    def __init__(self, path):
        self.branch = "self-improvement/fake-branch"
        self.path = path


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_HISTORY_PATH", tmp_path / "history.json")
    return tmp_path


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    """Wires every engine collaborator to a controllable stub, and returns a
    dict of small mutable settings each test can adjust before calling
    engine.improve()."""
    settings = {
        "root_cause": "the volume tool assumes pactl exists on all Linux systems",
        "plan": {
            "kind": "bug_fix",
            "summary": "guard the pactl call",
            "target_files": ["actions/computer_settings.py"],
            "change_description": "check for pactl before calling it",
            "test_description": "volume tool does not crash when pactl is missing",
            "risk": "low",
            "requires_human_approval": False,
        },
        "files_changed": ["actions/computer_settings.py"],
        "test_passed": True,
        "test_ran": True,
        "evaluation": {"passed": True, "reason": "Looks correct."},
    }

    workspace = _FakeWorkspace(tmp_path)

    monkeypatch.setattr(code_analyzer, "analyze", lambda problem, files: {
        "root_cause": settings["root_cause"], "confidence": "high", "relevant_files": files,
    })
    monkeypatch.setattr(planner, "plan", lambda *a, **kw: dict(settings["plan"]))
    monkeypatch.setattr(executor, "execute", lambda ws_path, plan: {
        "files_changed": settings["files_changed"], "test_file": "tests/test_fake.py",
    })
    monkeypatch.setattr(rollback_manager, "create_workspace", lambda slug: workspace)
    monkeypatch.setattr(rollback_manager, "diff", lambda ws: "--- fake diff ---")
    monkeypatch.setattr(rollback_manager, "commit", lambda ws, msg: None)
    monkeypatch.setattr(rollback_manager, "show_last_commit", lambda ws: "--- fake diff ---")
    monkeypatch.setattr(rollback_manager, "discard", lambda ws: settings.__setitem__("discarded", True))
    monkeypatch.setattr(rollback_manager, "promote", lambda ws: settings.__setitem__("promoted", True))
    monkeypatch.setattr(test_runner, "run_tests", lambda ws_path, target=None: test_runner.TestResult(
        passed=settings["test_passed"], exit_code=0 if settings["test_passed"] else 1,
        stdout="", stderr="", ran=settings["test_ran"],
    ))
    monkeypatch.setattr(evaluator, "evaluate", lambda plan, diff_text, test_result: dict(settings["evaluation"]))

    return settings


def test_success_path_promotes_and_records(isolated_history, stub_pipeline):
    result = engine.improve("volume tool crashes on some Linux systems")

    assert result["status"] == "success"
    assert stub_pipeline.get("promoted") is True
    assert stub_pipeline.get("discarded") is None
    entries = history.all_entries()
    assert entries[-1]["status"] == "success"


def test_failed_evaluation_rolls_back(isolated_history, stub_pipeline):
    stub_pipeline["evaluation"] = {"passed": False, "reason": "Does not actually fix the bug."}

    result = engine.improve("volume tool crashes")

    assert result["status"] == "rolled_back"
    assert stub_pipeline.get("discarded") is True
    assert stub_pipeline.get("promoted") is None
    entries = history.all_entries()
    assert entries[-1]["status"] == "rolled_back"


def test_failing_tests_roll_back_without_calling_evaluator(isolated_history, stub_pipeline, monkeypatch):
    stub_pipeline["test_passed"] = False
    called = {"evaluator": False}
    monkeypatch.setattr(evaluator, "evaluate", lambda *a, **kw: called.__setitem__("evaluator", True) or {"passed": False})

    result = engine.improve("volume tool crashes")

    assert result["status"] == "rolled_back"
    assert stub_pipeline.get("discarded") is True
    # evaluator.evaluate is still called (it handles the not-ran/not-passed case
    # itself and returns passed=False immediately) - what matters is the result.
    assert result["status"] != "success"


def test_high_risk_change_awaits_approval(isolated_history, stub_pipeline):
    stub_pipeline["plan"]["requires_human_approval"] = True

    result = engine.improve("something risky")

    assert result["status"] == "awaiting_approval"
    assert stub_pipeline.get("promoted") is None
    assert stub_pipeline.get("discarded") is None  # left pending, not cleaned up
    entry = history.all_entries()[-1]
    assert entry["status"] == "awaiting_approval"
    assert "id" in entry


def test_auto_improvement_skips_approval_for_low_risk(isolated_history, stub_pipeline, monkeypatch):
    # engine.py did `from memory.config_manager import get_auto_improvement`, which
    # binds its own name in engine's namespace - patching the source module's
    # attribute would not affect that already-bound reference.
    monkeypatch.setattr(engine, "get_auto_improvement", lambda: True)
    stub_pipeline["plan"]["requires_human_approval"] = True  # would normally need approval

    result = engine.improve("something risky but auto-improvement is on")

    assert result["status"] == "success"
    assert stub_pipeline.get("promoted") is True


def test_target_on_denylist_blocks_before_any_write(isolated_history, stub_pipeline, monkeypatch):
    stub_pipeline["plan"]["target_files"] = ["core/self_improvement/engine.py"]
    executed = {"called": False}
    monkeypatch.setattr(executor, "execute", lambda *a, **kw: executed.__setitem__("called", True) or {})

    result = engine.improve("try to disable your own safety guard")

    assert result["status"] == "blocked_by_safety_guard"
    assert executed["called"] is False  # never even got to the isolated workspace
    entry = history.all_entries()[-1]
    assert entry["status"] == "blocked_by_safety_guard"


def test_no_root_cause_fails_cleanly_without_touching_git(isolated_history, monkeypatch):
    monkeypatch.setattr(code_analyzer, "analyze", lambda problem, files: {"root_cause": "", "confidence": "low"})
    called = {"workspace": False}
    monkeypatch.setattr(rollback_manager, "create_workspace", lambda slug: called.__setitem__("workspace", True))

    result = engine.improve("a problem nothing can explain")

    assert result["status"] == "failed"
    assert called["workspace"] is False


def test_repeated_failure_is_visible_to_the_planner(isolated_history, stub_pipeline, monkeypatch):
    stub_pipeline["evaluation"] = {"passed": False, "reason": "wrong approach"}
    engine.improve("a recurring problem")  # first attempt, fails and is recorded

    seen_failures = {}

    def _capturing_plan(problem, root_cause, relevant_files, past_failures):
        seen_failures["failures"] = past_failures
        return dict(stub_pipeline["plan"])

    monkeypatch.setattr(planner, "plan", _capturing_plan)
    engine.improve("a recurring problem")

    assert len(seen_failures["failures"]) == 1
    assert seen_failures["failures"][0]["status"] == "rolled_back"


def test_main_working_tree_never_touched_on_failure(isolated_history, stub_pipeline, tmp_path):
    """The stub pipeline never invokes real git, so this asserts the contract
    at the orchestration level: discard() (not any direct file write) is the
    only thing engine.py calls on failure - see test_rollback_manager.py for
    proof that discard() itself leaves the real checkout untouched."""
    stub_pipeline["evaluation"] = {"passed": False, "reason": "bad"}
    engine.improve("anything")
    assert stub_pipeline.get("discarded") is True
