"""
core/coding_agent/agent_loop.py — the coding agent's
plan -> inspect project -> choose tool -> execute -> test -> read errors ->
fix -> test again -> finished loop.

Each iteration asks Gemini (SMART tier, via core/gemini.py - exactly like
actions/dev_agent.py and core/self_improvement/executor.py already do) for
ONE next tool call given the task and the running history of what has been
tried so far, then actually executes it through core/coding_agent/tools.py.
This performs real file and subprocess operations inside the sandboxed
workspace (core/coding_agent/workspace.py) - it is not a scripted demo.

Confirmation is handled the same way core/self_improvement/engine.py's
awaiting_approval and actions/self_improve.py's confirm.request already do: a
gated step returns immediately with a `resume` closure instead of blocking,
so core/confirm.py's on-screen banner can invoke it later without this
module knowing anything about threads or Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core import gemini

from . import permissions, tools, workspace

MAX_STEPS_DEFAULT = 20
_REPEAT_FAILURE_LIMIT = 3
_HISTORY_WINDOW = 8   # only the most recent steps go into the prompt - keeps it bounded

_TOOL_NAMES = (
    "read_file", "write_file", "edit_file", "list_directory", "search_code",
    "create_directory", "delete_file", "run_command", "run_tests",
    "get_project_structure",
)


@dataclass
class _State:
    task: str
    project_dir: Path
    player: object
    speak: object
    max_steps: int
    history: list = field(default_factory=list)
    step: int = 0


def _log(state: _State, msg: str) -> None:
    if not msg:
        return
    print(f"[CodingAgent] {msg}")
    if state.player:
        try:
            state.player.write_log(f"[CodingAgent] {msg}")
        except Exception:
            pass


def _history_text(state: _State) -> str:
    if not state.history:
        return "(no steps taken yet)"
    lines = []
    for i, h in enumerate(state.history, start=1):
        out = (h["output"] or "")[:1200]
        lines.append(f"Step {i}: {h['tool']}({h['args']}) -> "
                     f"{'OK' if h['ok'] else 'FAILED'}\n{out}")
    return "\n\n".join(lines[-_HISTORY_WINDOW:])


def _decide_next_step(state: _State) -> dict:
    structure = tools.get_project_structure(state.project_dir).output
    prompt = f"""You are JARVIS's coding agent, working ONLY inside this sandboxed project directory: {state.project_dir}
You cannot see or touch anything outside it.

Task: {state.task}

Current project structure:
{structure}

Steps already taken (most recent last):
{_history_text(state)}

Choose EXACTLY ONE next step. Reply with ONLY a JSON object - no markdown, no explanation.

To call a tool:
{{"tool": "<one of {', '.join(_TOOL_NAMES)}>", "args": {{...}}, "narration": "one short sentence, in the same language as the task, saying what you are about to do"}}

Tool arguments:
- read_file: {{"path": "relative/path"}}
- write_file: {{"path": "relative/path", "content": "full file content", "overwrite": true}}
- edit_file: {{"path": "relative/path", "content": "full NEW file content"}} (path must already exist)
- list_directory: {{"path": "relative/path, or empty for the project root"}}
- search_code: {{"query": "text to find", "path": "optional subpath", "glob": "optional filename glob e.g. *.py"}}
- create_directory: {{"path": "relative/path"}}
- delete_file: {{"path": "relative/path"}}
- run_command: {{"command": "shell command", "timeout": 60}}
- run_tests: {{"target": "optional path to one test file, omit to run everything"}}
- get_project_structure: {{}}

When the task is fully done (files written, it runs, tests pass if applicable), reply instead with:
{{"tool": "finished", "narration": "...", "summary": "...", "files_changed": ["..."], "tests_run": ["..."], "tests_passed": true, "remaining_issues": ""}}

If you are stuck and cannot make further progress, also reply with "finished", tests_passed false, and explain what remains in remaining_issues.

JSON:"""

    decision = gemini.as_json(prompt, tier=gemini.SMART, timeout_ms=45000, default=None)
    if not isinstance(decision, dict) or not decision.get("tool"):
        return {
            "tool": "finished",
            "narration": "",
            "summary": "The planning step returned a response that could not be understood.",
            "files_changed": [], "tests_run": [], "tests_passed": False,
            "remaining_issues": "The model's step-planning reply could not be parsed as JSON.",
        }
    return decision


def _execute_tool(tool: str, args: dict, state: _State) -> tools.ToolResult:
    args = args or {}
    if tool == "read_file":
        return tools.read_file(state.project_dir, args.get("path", ""))
    if tool == "write_file":
        return tools.write_file(state.project_dir, args.get("path", ""), args.get("content", ""),
                                overwrite=bool(args.get("overwrite", True)))
    if tool == "edit_file":
        return tools.edit_file(state.project_dir, args.get("path", ""), args.get("content", ""))
    if tool == "list_directory":
        return tools.list_directory(state.project_dir, args.get("path", ""))
    if tool == "search_code":
        return tools.search_code(state.project_dir, args.get("query", ""),
                                 args.get("path", ""), args.get("glob", "*"))
    if tool == "create_directory":
        return tools.create_directory(state.project_dir, args.get("path", ""))
    if tool == "delete_file":
        return tools.delete_file(state.project_dir, args.get("path", ""))
    if tool == "run_command":
        return tools.run_command(state.project_dir, args.get("command", ""),
                                 timeout=int(args.get("timeout") or 60))
    if tool == "run_tests":
        return tools.run_tests(state.project_dir, args.get("target") or None)
    if tool == "get_project_structure":
        return tools.get_project_structure(state.project_dir)
    return tools.ToolResult(False, f"Unknown tool '{tool}'.")


def _repeated_failure(state: _State) -> bool:
    if len(state.history) < _REPEAT_FAILURE_LIMIT:
        return False
    last = state.history[-_REPEAT_FAILURE_LIMIT:]
    if any(h["ok"] for h in last):
        return False
    sig = {(h["tool"], str(h["args"])) for h in last}
    return len(sig) == 1


def _files_touched(state: _State) -> list[str]:
    seen: list[str] = []
    for h in state.history:
        if h["tool"] in ("write_file", "edit_file", "create_directory", "delete_file") and h["ok"]:
            p = h["args"].get("path")
            if p and p not in seen:
                seen.append(p)
    return seen


def _tests_run(state: _State) -> list[str]:
    return [h["args"].get("target") or "(full suite)"
            for h in state.history if h["tool"] == "run_tests"]


def _derive_status(state: _State, decision: dict, forced: bool) -> str:
    if forced:
        return "partial" if any(h["ok"] for h in state.history) else "failed"
    if decision.get("tests_passed"):
        return "success"
    if any(h["tool"] == "run_tests" for h in state.history):
        return "partial" if any(h["ok"] for h in state.history) else "failed"
    # No tests exist for this task - fall back to whether recent steps held up.
    if state.history and all(h["ok"] for h in state.history[-3:]):
        return "success"
    return "partial" if any(h["ok"] for h in state.history) else "failed"


def _finalize(state: _State, decision: dict, forced: bool = False) -> dict:
    status = _derive_status(state, decision, forced)
    summary = decision.get("summary") or "Stopped."
    _log(state, f"Finished ({status}): {summary}")
    return {
        "status": status,
        "summary": summary,
        "files_changed": decision.get("files_changed") or _files_touched(state),
        "tests_run": decision.get("tests_run") or _tests_run(state),
        "tests_passed": bool(decision.get("tests_passed")),
        "remaining_issues": decision.get("remaining_issues") or "",
        "project_dir": str(state.project_dir),
        "steps": len(state.history),
    }


def _run_loop(state: _State) -> dict:
    while state.step < state.max_steps:
        decision = _decide_next_step(state)
        state.step += 1
        tool = decision.get("tool")
        _log(state, decision.get("narration") or "")

        if tool == "finished":
            return _finalize(state, decision)

        args = decision.get("args") or {}
        gate = permissions.check(tool, args, state.project_dir)

        if isinstance(gate, permissions.Denied):
            state.history.append({"tool": tool, "args": args, "ok": False, "output": gate.reason})
            _log(state, gate.reason)
            continue

        if isinstance(gate, permissions.Gate):
            def resume(_tool=tool, _args=args, _state=state):
                result = _execute_tool(_tool, _args, _state)
                _state.history.append({"tool": _tool, "args": _args,
                                       "ok": result.ok, "output": result.output})
                return _run_loop(_state)

            return {
                "status": "awaiting_confirmation",
                "confirm": {"key": gate.key, "title": gate.title, "detail": gate.detail},
                "resume": resume,
            }

        result = _execute_tool(tool, args, state)
        state.history.append({"tool": tool, "args": args, "ok": result.ok, "output": result.output})

        if _repeated_failure(state):
            return _finalize(state, {
                "summary": (f"Stopped after '{tool}' failed {_REPEAT_FAILURE_LIMIT} times in a "
                           f"row with the same arguments."),
                "tests_passed": False,
                "remaining_issues": state.history[-1]["output"][:500],
            }, forced=True)

    return _finalize(state, {
        "summary": f"Reached the {state.max_steps}-step limit before finishing.",
        "tests_passed": False,
        "remaining_issues": "The task may need to be continued or narrowed down.",
    }, forced=True)


def run_task(task: str, project_path: str | None = None, player=None, speak=None,
            max_steps: int = MAX_STEPS_DEFAULT) -> dict:
    """Runs the full agent loop for `task` inside the sandboxed workspace.

    Returns either a finished result dict ({"status": "success"/"partial"/
    "failed", "summary", "files_changed", "tests_run", "tests_passed",
    "remaining_issues", "project_dir", "steps"}), or, if a step needs
    approval, {"status": "awaiting_confirmation", "confirm": {...},
    "resume": callable() -> dict} - the caller (actions/coding_agent.py)
    wires "resume" into core/confirm.py exactly like self_improve() does."""
    root = workspace.get_workspace_root()
    project_dir = workspace.resolve_project_dir(project_path, root)
    project_dir.mkdir(parents=True, exist_ok=True)

    state = _State(task=task, project_dir=project_dir, player=player, speak=speak,
                   max_steps=max_steps)
    _log(state, f"Starting task in {project_dir}: {task}")
    return _run_loop(state)
