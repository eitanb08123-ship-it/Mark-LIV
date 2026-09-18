"""
core/coding_agent/agent_loop.py — the coding agent's
plan -> inspect project -> choose tool -> execute -> test -> read errors ->
fix -> test again -> finished loop, with Claude (core/claude_client.py) as
the coding brain.

Claude drives this with its OWN native tool-use, not a "reply with JSON"
convention: each turn it may call one or more of the real tools in
core/coding_agent/tools.py, or call the special `finished` tool with a
structured report. Tool execution, permission gating
(core/coding_agent/permissions.py) and the sandbox
(core/coding_agent/workspace.py) are completely unchanged by this - Claude
gets exactly the same guarded tools the rest of the app already built.

Bounded context: the running project structure and a short digest of past
steps are refreshed into the SYSTEM prompt every turn (cheap, always
current), while the raw Claude message history - the actual tool_use /
tool_result exchange Claude reasons over turn-to-turn - is pruned to the
last few rounds (_MAX_EXCHANGES_KEPT) rather than growing forever.

Confirmation is handled exactly the way core/self_improvement/engine.py's
awaiting_approval and actions/self_improve.py's confirm.request already do:
a gated tool call returns immediately with a `resume` closure instead of
blocking, so core/confirm.py's on-screen banner can invoke it later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core import claude_client

from . import permissions, tools, workspace
from memory.config_manager import get_coding_agent_max_steps

MAX_STEPS_DEFAULT = 20
_REPEAT_FAILURE_LIMIT = 3
_MAX_EXCHANGES_KEPT = 6     # assistant+tool_result round-trips kept in raw form
_DIGEST_LIMIT = 15          # one-line history entries kept in the system prompt
_MAX_TOOL_RESULT_CHARS = 4000

_FINISHED_TOOL = "finished"

_TOOLS_SCHEMA = [
    {
        "name": "read_file",
        "description": "Read a text file's contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the project directory."}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create a new file, or overwrite an existing one, with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project directory."},
                "content": {"type": "string", "description": "The full file content."},
                "overwrite": {"type": "boolean", "description": "Allow overwriting if it already exists (default true)."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace the COMPLETE contents of an existing file. The file must already exist - use write_file to create a new one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project directory."},
                "content": {"type": "string", "description": "The full NEW file content."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List the immediate contents of a directory.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the project directory, or empty for the root."}},
        },
    },
    {
        "name": "search_code",
        "description": "Search for a text string across files in the project (optionally scoped to a subpath/glob).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "description": "Optional subpath to search within."},
                "glob": {"type": "string", "description": "Optional filename glob, e.g. '*.py'."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_directory",
        "description": "Create a directory (and any missing parents).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the project directory."}},
            "required": ["path"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file (soft-deleted into .jarvis_trash/, recoverable). Always asks for on-screen confirmation first.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the project directory."}},
            "required": ["path"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command with the project directory as its working directory. May require on-screen confirmation for risky or install commands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 60)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run pytest. Omit target to run the whole suite; 'no tests collected' counts as a failure, not a pass.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Optional path to one test file."}},
        },
    },
    {
        "name": "get_project_structure",
        "description": "Get a depth-limited directory tree of the project. Also refreshed automatically into your system prompt every turn.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": _FINISHED_TOOL,
        "description": (
            "Call this, and ONLY this, when the task is fully complete OR you cannot make "
            "further progress and must stop. Always call it eventually - never simply reply "
            "with plain text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One or two sentences, in the same language as the task."},
                "files_changed": {"type": "array", "items": {"type": "string"}},
                "tests_run": {"type": "array", "items": {"type": "string"}},
                "tests_passed": {"type": "boolean"},
                "remaining_issues": {"type": "string", "description": "'' if nothing is left."},
            },
            "required": ["summary", "tests_passed"],
        },
    },
]

_SYSTEM_TEMPLATE = """You are JARVIS's coding agent, working ONLY inside this sandboxed project directory: {project_dir}
You cannot see or touch anything outside it - every tool call is checked against that boundary in code.

Task: {task}

Current project structure:
{structure}

Recent steps so far (most recent last; earlier ones were dropped to keep context bounded):
{digest}

Rules:
- Inspect before you change anything you have not already read this session (get_project_structure / list_directory / read_file / search_code).
- Prefer edit_file over rewriting unrelated parts of a file.
- After changing code that can run, run it (run_command) or test it (run_tests) before declaring success.
- If a run or test fails, read the error output and fix the actual cause, then run/test again.
- Before EVERY tool call, and in your final `finished` call, include one short sentence of plain text narration, in the same language as the task, saying what you are doing (e.g. "checking the project structure...", "writing main.py...", "running the tests...", "found an error, fixing it...").
- Call `finished` exactly once, when done or truly stuck - never stop by replying with plain text alone.
"""


@dataclass
class _State:
    task: str
    project_dir: Path
    player: object
    speak: object
    max_steps: int
    messages: list = field(default_factory=list)
    history: list = field(default_factory=list)   # [{"tool","args","ok","output"}, ...] - full record, never pruned
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


def _digest_text(state: _State) -> str:
    if not state.history:
        return "(no steps taken yet)"
    lines = []
    for i, h in enumerate(state.history, start=1):
        out = (h["output"] or "").replace("\n", " ")[:200]
        lines.append(f"{i}. {h['tool']}({h['args']}) -> {'OK' if h['ok'] else 'FAILED'}: {out}")
    return "\n".join(lines[-_DIGEST_LIMIT:])


def _system_prompt(state: _State) -> str:
    structure = tools.get_project_structure(state.project_dir).output
    return _SYSTEM_TEMPLATE.format(
        project_dir=state.project_dir, task=state.task,
        structure=structure, digest=_digest_text(state),
    )


def _prune_messages(state: _State) -> None:
    """Keeps messages[0] (the original task) plus only the last
    _MAX_EXCHANGES_KEPT (assistant, tool_result) round-trips. Each round
    appends exactly one assistant message then one user (tool_result)
    message, so the body is always an even-length, alternation-safe
    sequence - slicing from the end never breaks the required
    user/assistant alternation."""
    if not state.messages:
        return
    head, body = state.messages[0], state.messages[1:]
    max_len = _MAX_EXCHANGES_KEPT * 2
    if len(body) > max_len:
        body = body[-max_len:]
    state.messages = [head] + body


def _tool_result_block(tool_use_id: str, text: str, is_error: bool = False) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": (text or "")[:_MAX_TOOL_RESULT_CHARS],
        "is_error": bool(is_error),
    }


def _execute_tool(name: str, args: dict, state: _State) -> tools.ToolResult:
    args = args or {}
    if name == "read_file":
        return tools.read_file(state.project_dir, args.get("path", ""))
    if name == "write_file":
        return tools.write_file(state.project_dir, args.get("path", ""), args.get("content", ""),
                                overwrite=bool(args.get("overwrite", True)))
    if name == "edit_file":
        return tools.edit_file(state.project_dir, args.get("path", ""), args.get("content", ""))
    if name == "list_directory":
        return tools.list_directory(state.project_dir, args.get("path", ""))
    if name == "search_code":
        return tools.search_code(state.project_dir, args.get("query", ""),
                                 args.get("path", ""), args.get("glob", "*"))
    if name == "create_directory":
        return tools.create_directory(state.project_dir, args.get("path", ""))
    if name == "delete_file":
        return tools.delete_file(state.project_dir, args.get("path", ""))
    if name == "run_command":
        return tools.run_command(state.project_dir, args.get("command", ""),
                                 timeout=int(args.get("timeout") or 60))
    if name == "run_tests":
        return tools.run_tests(state.project_dir, args.get("target") or None)
    if name == "get_project_structure":
        return tools.get_project_structure(state.project_dir)
    return tools.ToolResult(False, f"Unknown tool '{name}'.")


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
    return [h["args"].get("target") or "(full suite)" for h in state.history if h["tool"] == "run_tests"]


def _derive_status(state: _State, report: dict, forced: bool) -> str:
    if forced:
        # A step-limit or repeated-failure abort is never "success", even if
        # every individual step so far happened to succeed - the task itself
        # was not declared done.
        return "partial" if any(h["ok"] for h in state.history) else "failed"
    if report.get("tests_passed"):
        return "success"
    if any(h["tool"] == "run_tests" for h in state.history):
        return "partial" if any(h["ok"] for h in state.history) else "failed"
    if state.history and all(h["ok"] for h in state.history[-3:]):
        return "success"
    return "partial" if any(h["ok"] for h in state.history) else "failed"


def _finalize(state: _State, report: dict, forced: bool = False) -> dict:
    status = _derive_status(state, report, forced)
    summary = report.get("summary") or "Stopped."
    _log(state, f"Finished ({status}): {summary}")
    return {
        "status": status,
        "summary": summary,
        "files_changed": report.get("files_changed") or _files_touched(state),
        "tests_run": report.get("tests_run") or _tests_run(state),
        "tests_passed": bool(report.get("tests_passed")),
        "remaining_issues": report.get("remaining_issues") or "",
        "project_dir": str(state.project_dir),
        "steps": len(state.history),
    }


def _finalize_forced(state: _State, reason: str) -> dict:
    return _finalize(state, {"summary": reason, "tests_passed": False, "remaining_issues": reason},
                     forced=True)


def _process_tool_uses(state: _State, tool_use_blocks: list, results_so_far: list) -> dict:
    for i, block in enumerate(tool_use_blocks):
        if block.name == _FINISHED_TOOL:
            return _finalize(state, dict(block.input or {}))

        gate = permissions.check(block.name, block.input, state.project_dir)

        if isinstance(gate, permissions.Denied):
            state.history.append({"tool": block.name, "args": block.input, "ok": False, "output": gate.reason})
            _log(state, gate.reason)
            results_so_far.append(_tool_result_block(block.id, gate.reason, is_error=True))
            continue

        if isinstance(gate, permissions.Gate):
            remaining = tool_use_blocks[i + 1:]
            collected = list(results_so_far)

            def resume(_block=block, _remaining=remaining, _collected=collected, _state=state):
                result = _execute_tool(_block.name, _block.input, _state)
                _state.history.append({"tool": _block.name, "args": _block.input,
                                       "ok": result.ok, "output": result.output})
                _collected.append(_tool_result_block(_block.id, result.output, is_error=not result.ok))
                return _process_tool_uses(_state, _remaining, _collected)

            return {
                "status": "awaiting_confirmation",
                "confirm": {"key": gate.key, "title": gate.title, "detail": gate.detail},
                "resume": resume,
            }

        result = _execute_tool(block.name, block.input, state)
        state.history.append({"tool": block.name, "args": block.input, "ok": result.ok, "output": result.output})
        results_so_far.append(_tool_result_block(block.id, result.output, is_error=not result.ok))

    state.messages.append({"role": "user", "content": results_so_far})
    _prune_messages(state)
    return _run_loop(state)


def _run_loop(state: _State) -> dict:
    if _repeated_failure(state):
        return _finalize_forced(state, f"Stopped after the same tool call failed "
                                       f"{_REPEAT_FAILURE_LIMIT} times in a row.")
    if state.step >= state.max_steps:
        return _finalize_forced(state, f"Reached the {state.max_steps}-step limit before finishing.")

    try:
        response = claude_client.call_with_tools(
            system=_system_prompt(state), messages=state.messages, tools=_TOOLS_SCHEMA,
        )
    except RuntimeError as e:
        return _finalize_forced(state, str(e))

    state.step += 1
    blocks = list(response.content)
    state.messages.append({"role": "assistant", "content": blocks})

    for b in blocks:
        if getattr(b, "type", None) == "text" and getattr(b, "text", "").strip():
            _log(state, b.text.strip())

    tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
    if not tool_uses:
        text = " ".join(b.text.strip() for b in blocks
                        if getattr(b, "type", None) == "text" and b.text.strip())
        return _finalize_forced(state, text or "Stopped without calling a tool or finishing.")

    return _process_tool_uses(state, tool_uses, [])


def run_task(task: str, project_path: str | None = None, player=None, speak=None,
            max_steps: int | None = None) -> dict:
    """Runs the full agent loop for `task` inside the sandboxed workspace,
    with Claude choosing and calling the tools.

    Returns either a finished result dict ({"status": "success"/"partial"/
    "failed", "summary", "files_changed", "tests_run", "tests_passed",
    "remaining_issues", "project_dir", "steps"}), or, if a step needs
    approval, {"status": "awaiting_confirmation", "confirm": {...},
    "resume": callable() -> dict} - actions/coding_agent.py wires "resume"
    into core/confirm.py exactly like self_improve() does."""
    root = workspace.get_workspace_root()
    project_dir = workspace.resolve_project_dir(project_path, root)
    project_dir.mkdir(parents=True, exist_ok=True)

    state = _State(
        task=task, project_dir=project_dir, player=player, speak=speak,
        max_steps=max_steps if max_steps is not None else get_coding_agent_max_steps(),
        messages=[{"role": "user", "content": f"Task: {task}"}],
    )

    if not claude_client.is_configured():
        return _finalize_forced(state, f"Claude is not available: {claude_client.why_not_configured()}")

    _log(state, f"Starting task in {project_dir}: {task}")
    return _run_loop(state)
