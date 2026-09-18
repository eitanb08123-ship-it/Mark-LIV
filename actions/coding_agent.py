"""
actions/coding_agent.py — the general-purpose Coding Agent: given a natural
language task ("build me a game", "fix the bug in my project", "add a
button to the app", "change the code so that...", "check why this doesn't
work", "create a new project", "install a missing dependency", "review the
code and find problems"), it inspects the target project, reads/writes/
edits files, runs commands and tests, reads back any errors, and retries -
inside a sandboxed workspace (core/coding_agent/workspace.py) - until the
task is done or it genuinely cannot make further progress.

This is deliberately separate from self_improve/self_analyze: those work
ONLY on Mark LIV's own source tree through a git-branch-and-approval
pipeline built for the assistant improving itself. This one works on the
user's own projects inside a plain sandboxed folder - different risk
profile, different guard (core/coding_agent/permissions.py), same
confirmation mechanism (core/confirm.py) for anything irreversible.
"""
from __future__ import annotations

from core import confirm
from core.coding_agent import agent_loop

_STATUS_LABEL = {
    "success": "Done",
    "partial": "Partly done",
    "failed": "Could not finish",
}


def _format_report(result: dict) -> str:
    status = result.get("status", "failed")
    lines = [f"[{_STATUS_LABEL.get(status, status.upper())}] {result.get('summary', '')}"]

    files = result.get("files_changed") or []
    if files:
        lines.append("Files changed: " + ", ".join(files))

    tests = result.get("tests_run") or []
    if tests:
        outcome = "passed" if result.get("tests_passed") else "did not all pass"
        lines.append(f"Tests run ({', '.join(tests)}): {outcome}")

    remaining = result.get("remaining_issues")
    if remaining:
        lines.append(f"Remaining: {remaining}")

    project_dir = result.get("project_dir")
    if project_dir:
        lines.append(f"Location: {project_dir}")

    return "\n".join(lines)


def _handle_result(result: dict, player=None) -> str:
    if result.get("status") != "awaiting_confirmation":
        report = _format_report(result)
        if player:
            try:
                player.write_log(f"[CodingAgent] {report}")
            except Exception:
                pass
        return report

    c = result["confirm"]
    resume = result["resume"]
    if player:
        try:
            player.write_log(f"[CodingAgent] Awaiting confirmation: {c['title']}")
        except Exception:
            pass
    return confirm.request(
        key=c["key"],
        title=c["title"],
        detail=c["detail"],
        run=lambda: _handle_result(resume(), player),
    )


def coding_agent(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    task = p.get("task", "").strip()
    project_path = p.get("project_path", "").strip() or None

    if not task:
        return "I need a description of what to build, fix, or change."

    if player:
        try:
            player.write_log(f"[CodingAgent] Task: {task}")
        except Exception:
            pass

    try:
        result = agent_loop.run_task(task, project_path=project_path, player=player, speak=speak)
    except Exception as e:
        return f"Coding agent failed unexpectedly: {e}"

    return _handle_result(result, player)


TOOL = {
    "name": "coding_agent",
    "description": (
        "A real, general-purpose coding agent for the user's OWN projects "
        "(not Mark LIV's own source - use self_improve for that). Handles "
        "natural requests like 'create a Python game', 'build me a website', "
        "'create a Python file and run it', 'fix the bug in my project', "
        "'add a button to the app', 'change the code so that...', 'check why "
        "this doesn't work', 'create a new project', 'install a missing "
        "dependency', or 'review the code and find problems'. It inspects "
        "the project, reads/writes/edits files, runs commands and tests, "
        "reads back errors, and retries across multiple steps until the task "
        "is done - all inside a sandboxed workspace folder. Destructive or "
        "risky steps (deleting a file, running an arbitrary shell command, "
        "installing a dependency) stop for on-screen confirmation first, "
        "unless settings enabled auto-approval for that category."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "task": {
                "type": "STRING",
                "description": "What to build, fix, change, or investigate, in the user's own words.",
            },
            "project_path": {
                "type": "STRING",
                "description": (
                    "Optional. Name of an existing project folder inside the coding "
                    "agent's workspace (or an absolute path already inside it) to work "
                    "on. Omit to work at the workspace root, or when creating something "
                    "brand new."
                ),
            },
        },
        "required": ["task"],
    },
    "handler": coding_agent,
}
