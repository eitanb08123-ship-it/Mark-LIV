"""
core/coding_agent/agent_loop.py orchestration tests. Only the LLM planning
step (gemini.as_json) is mocked with a scripted sequence of decisions -
everything else (tool execution, permission gating, undo) runs for real
inside a tmp_path workspace, the same way test_test_runner.py exercises a
real subprocess rather than mocking it.
"""
import pytest

from core import undo
from core.coding_agent import agent_loop, permissions, workspace


@pytest.fixture(autouse=True)
def _clear_undo():
    undo.clear()
    yield
    undo.clear()


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "_get_configured_root", lambda: str(tmp_path))
    # Deterministic regardless of what the real config file on disk says.
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: False)
    monkeypatch.setattr(permissions, "get_coding_agent_auto_install", lambda: False)
    return tmp_path


def _scripted(monkeypatch, decisions):
    it = iter(decisions)
    monkeypatch.setattr(agent_loop.gemini, "as_json", lambda prompt, **kw: next(it))


def test_simple_write_then_finish(project_dir, monkeypatch):
    _scripted(monkeypatch, [
        {"tool": "write_file", "args": {"path": "main.py", "content": "print('hi')"},
         "narration": "Writing main.py"},
        {"tool": "finished", "narration": "Done", "summary": "Wrote main.py",
         "files_changed": ["main.py"], "tests_run": [], "tests_passed": True,
         "remaining_issues": ""},
    ])

    result = agent_loop.run_task("create a hello world script", project_path="proj")

    assert result["status"] == "success"
    assert result["files_changed"] == ["main.py"]
    assert (project_dir / "proj" / "main.py").read_text() == "print('hi')"


def test_run_command_pauses_for_confirmation(project_dir, monkeypatch):
    _scripted(monkeypatch, [
        {"tool": "run_command", "args": {"command": "python main.py"}, "narration": "Running it"},
    ])

    result = agent_loop.run_task("run the script", project_path="proj")

    assert result["status"] == "awaiting_confirmation"
    assert result["confirm"]["title"]
    assert callable(result["resume"])


def test_resume_after_confirmation_continues_the_loop(project_dir, monkeypatch):
    (project_dir / "proj").mkdir()
    (project_dir / "proj" / "main.py").write_text("print('ran')")

    _scripted(monkeypatch, [
        {"tool": "run_command", "args": {"command": "python main.py"}, "narration": "Running it"},
        {"tool": "finished", "narration": "Done", "summary": "Ran it", "tests_passed": True,
         "files_changed": [], "tests_run": [], "remaining_issues": ""},
    ])

    paused = agent_loop.run_task("run the script", project_path="proj")
    assert paused["status"] == "awaiting_confirmation"

    result = paused["resume"]()
    assert result["status"] == "success"


def test_malformed_llm_response_finishes_cleanly(project_dir, monkeypatch):
    monkeypatch.setattr(agent_loop.gemini, "as_json", lambda prompt, **kw: None)

    result = agent_loop.run_task("do something", project_path="proj")

    assert result["status"] == "failed"
    assert result["steps"] == 0


def test_repeated_identical_failure_stops_the_loop(project_dir, monkeypatch):
    decision = {"tool": "read_file", "args": {"path": "missing.py"}, "narration": "Reading"}
    monkeypatch.setattr(agent_loop.gemini, "as_json", lambda prompt, **kw: dict(decision))

    result = agent_loop.run_task("read a file that will never exist", project_path="proj")

    assert result["status"] in ("failed", "partial")
    assert result["steps"] == 3


def test_hard_denied_command_is_skipped_not_paused(project_dir, monkeypatch):
    _scripted(monkeypatch, [
        {"tool": "run_command", "args": {"command": "sudo rm -rf /"}, "narration": "..."},
        {"tool": "finished", "narration": "Done", "summary": "gave up", "tests_passed": False,
         "files_changed": [], "tests_run": [], "remaining_issues": "blocked"},
    ])

    result = agent_loop.run_task("do something destructive", project_path="proj")

    assert result["status"] in ("failed", "partial")


def test_step_limit_stops_a_never_finishing_loop(project_dir, monkeypatch):
    decision = {"tool": "list_directory", "args": {"path": ""}, "narration": "Looking around"}
    monkeypatch.setattr(agent_loop.gemini, "as_json", lambda prompt, **kw: dict(decision))

    result = agent_loop.run_task("loop forever", project_path="proj", max_steps=5)

    assert result["steps"] == 5
    assert result["status"] in ("failed", "partial")
