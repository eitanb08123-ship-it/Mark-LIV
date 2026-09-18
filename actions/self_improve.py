"""
actions/self_improve.py — runs one full self-improvement attempt for a
named problem. See core/self_improvement/engine.py:improve().

Approval routing: when improve() stops at "awaiting_approval", this uses
core/confirm.py's existing on-screen gate (the same one shutdown/restart
use) so the user sees CONFIRM/CANCEL and the change is only kept if they
press it. Pressing CANCEL leaves the attempt recorded as pending - it can
still be resolved later with self_improvement_resolve, which does not
depend on the banner still being on screen.
"""
from __future__ import annotations

from core import confirm
from core.self_improvement import approve, improve

_STATUS_MESSAGES = {
    "success": "Applied and kept on branch '{branch}'.",
    "rolled_back": "Tried it, but it didn't hold up: {reason}",
    "blocked_by_safety_guard": "Stopped before touching anything: {reason}",
    "failed": "Could not complete this: {reason}",
}


def self_improve(parameters: dict, player=None) -> str:
    problem = parameters.get("problem", "").strip()
    if not problem:
        return "I need a specific problem or behavior to improve - what should I work on?"

    try:
        result = improve(problem)
    except Exception as e:
        return f"Self-improvement attempt failed unexpectedly: {e}"

    status = result.get("status", "failed")

    if status == "awaiting_approval":
        entry_id = result.get("id")
        summary = result.get("solution", problem)
        diff_preview = (result.get("diff") or "")[:1500]
        title = f"Approve self-improvement: {summary}"
        detail = (
            f"Problem: {problem}\n"
            f"Root cause: {result.get('root_cause', '')}\n"
            f"Files changed: {', '.join(result.get('files_changed', []))}\n"
            f"Tests: {'passed' if result.get('test_passed') else 'did not run'}\n\n"
            f"{diff_preview}"
        )
        if player:
            try:
                player.write_log(f"[SELF-IMPROVEMENT] Awaiting approval (id={entry_id}): {summary}")
            except Exception:
                pass
        return confirm.request(
            key=f"self_improve_{entry_id}",
            title=title,
            detail=detail,
            run=lambda: approve(entry_id).get("reason", "Approved."),
        )

    message = _STATUS_MESSAGES.get(status, "Unrecognized result: {reason}").format(
        branch=result.get("branch", ""), reason=result.get("reason", ""),
    )
    if player:
        try:
            player.write_log(f"[SELF-IMPROVEMENT] {status.upper()}: {message}")
        except Exception:
            pass
    return message


TOOL = {
    "name": "self_improve",
    "description": (
        "Attempt to actually fix or improve a specific problem in the assistant's "
        "own code: analyzes the root cause, writes a fix in an isolated git "
        "branch, writes and runs a test for it, and only keeps it if the test "
        "passes and an independent review agrees. Significant changes wait for "
        "your on-screen approval before being kept - nothing overwrites the "
        "live app directly. Use when the user wants an actual fix attempted, "
        "not just a report (use self_analyze for that)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "problem": {
                "type": "STRING",
                "description": "The specific problem or limitation to fix (e.g. 'the volume tool fails on Linux').",
            },
        },
        "required": ["problem"],
    },
    "handler": self_improve,
}
