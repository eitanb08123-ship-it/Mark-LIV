"""
core/coding_agent/agent_loop.py orchestration tests. Only the coding brain
(claude_client.call_with_tools) is mocked, with fake Anthropic-shaped content
blocks - everything else (tool execution, permission gating, undo, message
pruning) runs for real inside a tmp_path workspace, the same way
test_test_runner.py exercises a real subprocess rather than mocking it.
"""
from types import SimpleNamespace

import pytest

from core import undo
from core.coding_agent import agent_loop, permissions, workspace
from memory import config_manager


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _response(*blocks):
    return SimpleNamespace(content=list(blocks))


@pytest.fixture(autouse=True)
def _clear_undo():
    undo.clear()
    yield
    undo.clear()


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "_get_configured_root", lambda: str(tmp_path))
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: False)
    monkeypatch.setattr(permissions, "get_coding_agent_auto_install", lambda: False)
    monkeypatch.setattr(agent_loop.claude_client, "is_configured", lambda: True)
    return tmp_path


def _scripted(monkeypatch, responses):
    it = iter(responses)
    monkeypatch.setattr(agent_loop.claude_client, "call_with_tools", lambda **kw: next(it))


def test_simple_write_then_finish(project_dir, monkeypatch):
    _scripted(monkeypatch, [
        _response(_text("Writing main.py"),
                 _tool_use("t1", "write_file", {"path": "main.py", "content": "print('hi')"})),
        _response(_text("Done"),
                 _tool_use("t2", "finished", {"summary": "Wrote main.py", "tests_passed": True,
                                              "files_changed": ["main.py"], "tests_run": [],
                                              "remaining_issues": ""})),
    ])

    result = agent_loop.run_task("create a hello world script", project_path="proj")

    assert result["status"] == "success"
    assert result["files_changed"] == ["main.py"]
    assert (project_dir / "proj" / "main.py").read_text() == "print('hi')"


def test_run_command_pauses_for_confirmation(project_dir, monkeypatch):
    _scripted(monkeypatch, [
        _response(_tool_use("t1", "run_command", {"command": "python main.py"})),
    ])

    result = agent_loop.run_task("run the script", project_path="proj")

    assert result["status"] == "awaiting_confirmation"
    assert result["confirm"]["title"]
    assert callable(result["resume"])


def test_resume_after_confirmation_continues_the_loop(project_dir, monkeypatch):
    (project_dir / "proj").mkdir()
    (project_dir / "proj" / "main.py").write_text("print('ran')")

    _scripted(monkeypatch, [
        _response(_tool_use("t1", "run_command", {"command": "python main.py"})),
        _response(_tool_use("t2", "finished", {"summary": "Ran it", "tests_passed": True,
                                               "files_changed": [], "tests_run": [],
                                               "remaining_issues": ""})),
    ])

    paused = agent_loop.run_task("run the script", project_path="proj")
    assert paused["status"] == "awaiting_confirmation"

    result = paused["resume"]()
    assert result["status"] == "success"


def test_claude_call_failure_finishes_cleanly_instead_of_crashing(project_dir, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("Claude call failed: boom")
    monkeypatch.setattr(agent_loop.claude_client, "call_with_tools", _boom)

    result = agent_loop.run_task("do something", project_path="proj")

    assert result["status"] == "failed"
    assert result["steps"] == 0
    assert "boom" in result["remaining_issues"]


def test_not_configured_short_circuits_without_calling_anything(project_dir, monkeypatch):
    monkeypatch.setattr(agent_loop.claude_client, "is_configured", lambda: False)
    monkeypatch.setattr(agent_loop.claude_client, "why_not_configured", lambda: "ANTHROPIC_API_KEY is not set.")
    monkeypatch.setattr(config_manager, "is_configured", lambda: False)
    called = {"claude": False, "gemini": False}
    monkeypatch.setattr(agent_loop.claude_client, "call_with_tools",
                        lambda **kw: called.__setitem__("claude", True))
    monkeypatch.setattr(agent_loop.gemini, "as_json",
                        lambda *a, **kw: called.__setitem__("gemini", True))

    result = agent_loop.run_task("do something", project_path="proj")

    assert result["status"] == "failed"
    assert called == {"claude": False, "gemini": False}
    assert "ANTHROPIC_API_KEY" in result["remaining_issues"]


# ── Free/Gemini fallback ─────────────────────────────────────────────────────
# When ANTHROPIC_API_KEY isn't set but a Gemini key is (the one every other
# JARVIS action already needs), the coding agent still works - just through
# the JSON-decision convention core/gemini.py is called with, instead of
# Claude's native tool-use.

@pytest.fixture
def gemini_project_dir(project_dir, monkeypatch):
    monkeypatch.setattr(agent_loop.claude_client, "is_configured", lambda: False)
    monkeypatch.setattr(agent_loop.claude_client, "why_not_configured", lambda: "ANTHROPIC_API_KEY is not set.")
    monkeypatch.setattr(config_manager, "is_configured", lambda: True)
    return project_dir


def _scripted_gemini(monkeypatch, decisions):
    it = iter(decisions)
    monkeypatch.setattr(agent_loop.gemini, "as_json", lambda *a, **kw: next(it))


def test_gemini_fallback_is_used_when_claude_is_not_configured(gemini_project_dir, monkeypatch):
    called = {"claude": False}
    monkeypatch.setattr(agent_loop.claude_client, "call_with_tools",
                        lambda **kw: called.__setitem__("claude", True))
    _scripted_gemini(monkeypatch, [
        {"tool": "write_file", "args": {"path": "main.py", "content": "print('hi')"},
         "narration": "Writing main.py"},
        {"tool": "finished", "narration": "Done", "summary": "Wrote main.py", "tests_passed": True,
         "files_changed": ["main.py"], "tests_run": [], "remaining_issues": ""},
    ])

    result = agent_loop.run_task("create a hello world script", project_path="proj")

    assert called["claude"] is False
    assert result["status"] == "success"
    assert (gemini_project_dir / "proj" / "main.py").read_text() == "print('hi')"


def test_gemini_fallback_run_command_pauses_for_confirmation(gemini_project_dir, monkeypatch):
    _scripted_gemini(monkeypatch, [
        {"tool": "run_command", "args": {"command": "python main.py"}, "narration": "Running it"},
    ])

    result = agent_loop.run_task("run the script", project_path="proj")

    assert result["status"] == "awaiting_confirmation"
    assert callable(result["resume"])


def test_gemini_fallback_malformed_reply_finishes_cleanly(gemini_project_dir, monkeypatch):
    monkeypatch.setattr(agent_loop.gemini, "as_json", lambda *a, **kw: None)

    result = agent_loop.run_task("do something", project_path="proj")

    assert result["status"] == "failed"
    assert result["steps"] == 0


def test_gemini_fallback_hard_denied_command_is_skipped_not_paused(gemini_project_dir, monkeypatch):
    _scripted_gemini(monkeypatch, [
        {"tool": "run_command", "args": {"command": "sudo rm -rf /"}, "narration": "..."},
        {"tool": "finished", "narration": "Done", "summary": "gave up", "tests_passed": False,
         "files_changed": [], "tests_run": [], "remaining_issues": "blocked"},
    ])

    result = agent_loop.run_task("do something destructive", project_path="proj")

    assert result["status"] in ("failed", "partial")


def test_reply_with_no_tool_call_finishes_instead_of_looping(project_dir, monkeypatch):
    _scripted(monkeypatch, [_response(_text("I am not sure how to proceed."))])

    result = agent_loop.run_task("do something ambiguous", project_path="proj")

    assert result["status"] in ("failed", "partial")
    assert result["steps"] == 0


def test_repeated_identical_failure_stops_the_loop(project_dir, monkeypatch):
    response = _response(_tool_use("t1", "read_file", {"path": "missing.py"}))
    monkeypatch.setattr(agent_loop.claude_client, "call_with_tools", lambda **kw: response)

    result = agent_loop.run_task("read a file that will never exist", project_path="proj")

    assert result["status"] in ("failed", "partial")
    assert result["steps"] == 3


def test_hard_denied_command_is_skipped_not_paused(project_dir, monkeypatch):
    _scripted(monkeypatch, [
        _response(_tool_use("t1", "run_command", {"command": "sudo rm -rf /"})),
        _response(_tool_use("t2", "finished", {"summary": "gave up", "tests_passed": False,
                                               "files_changed": [], "tests_run": [],
                                               "remaining_issues": "blocked"})),
    ])

    result = agent_loop.run_task("do something destructive", project_path="proj")

    assert result["status"] in ("failed", "partial")


def test_step_limit_stops_a_never_finishing_loop(project_dir, monkeypatch):
    response = _response(_tool_use("t1", "list_directory", {"path": ""}))
    monkeypatch.setattr(agent_loop.claude_client, "call_with_tools", lambda **kw: response)

    result = agent_loop.run_task("loop forever", project_path="proj", max_steps=5)

    assert result["steps"] == 5
    assert result["status"] in ("failed", "partial")


def test_multiple_tool_uses_in_one_turn_are_all_executed(project_dir, monkeypatch):
    _scripted(monkeypatch, [
        _response(
            _tool_use("t1", "write_file", {"path": "a.py", "content": "x = 1"}),
            _tool_use("t2", "write_file", {"path": "b.py", "content": "y = 2"}),
        ),
        _response(_tool_use("t3", "finished", {"summary": "done", "tests_passed": True,
                                               "files_changed": ["a.py", "b.py"], "tests_run": [],
                                               "remaining_issues": ""})),
    ])

    result = agent_loop.run_task("write two files", project_path="proj")

    assert result["status"] == "success"
    assert (project_dir / "proj" / "a.py").read_text() == "x = 1"
    assert (project_dir / "proj" / "b.py").read_text() == "y = 2"


def test_message_history_is_pruned_but_bounded_and_alternating(project_dir, monkeypatch):
    calls = {"n": 0}
    seen_message_lengths = []

    def _fake(**kw):
        calls["n"] += 1
        seen_message_lengths.append(len(kw["messages"]))
        roles = [m["role"] for m in kw["messages"]]
        assert roles[0] == "user"
        for a, b in zip(roles, roles[1:]):
            assert a != b, "roles must strictly alternate for the Anthropic API"
        if calls["n"] >= 10:
            return _response(_tool_use(f"t{calls['n']}", "finished",
                                       {"summary": "done", "tests_passed": True,
                                        "files_changed": [], "tests_run": [], "remaining_issues": ""}))
        return _response(_tool_use(f"t{calls['n']}", "list_directory", {"path": ""}))

    monkeypatch.setattr(agent_loop.claude_client, "call_with_tools", _fake)

    result = agent_loop.run_task("do many small steps", project_path="proj", max_steps=15)

    assert result["status"] == "success"
    # 9 list_directory rounds actually executed before the 10th call's
    # `finished` - which itself is never added to the executed-tool history.
    assert result["steps"] == 9
    # ...while the raw Claude conversation sent on each call was kept bounded,
    # even though 10 rounds happened.
    max_len = 1 + agent_loop._MAX_EXCHANGES_KEPT * 2
    assert max(seen_message_lengths) <= max_len
    # 10 rounds with no pruning would have reached 1 + 2*9 = 19 messages by the last call.
    assert seen_message_lengths[-1] < 19
