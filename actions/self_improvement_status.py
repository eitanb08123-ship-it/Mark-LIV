"""
actions/self_improvement_status.py — reports on past and pending
self-improvement attempts, from core/self_improvement/history.py.
"""
from __future__ import annotations

from core.self_improvement import history

_MAX_LISTED = 5


def self_improvement_status(parameters: dict, player=None) -> str:
    entries = history.all_entries()
    if not entries:
        return "No self-improvement attempts have been made yet."

    pending = [e for e in entries if e.get("status") == "awaiting_approval"]
    recent = entries[-_MAX_LISTED:]

    lines = []
    if pending:
        lines.append(f"{len(pending)} attempt(s) awaiting your approval:")
        for e in pending:
            lines.append(f"  [{e['id']}] {e.get('solution', e.get('problem', '?'))}")
    lines.append(f"Last {len(recent)} attempt(s):")
    for e in reversed(recent):
        lines.append(f"  [{e['id']}] {e.get('status', '?')} - {e.get('solution', e.get('problem', '?'))}")

    result = "\n".join(lines)
    if player:
        try:
            player.write_log(f"[SELF-IMPROVEMENT] Status: {result}")
        except Exception:
            pass
    return result


TOOL = {
    "name": "self_improvement_status",
    "description": (
        "List past and pending self-improvement attempts - including any "
        "waiting for approval. Use for requests like 'what have you improved', "
        "'any pending approvals', or 'self improvement history'."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": self_improvement_status,
}
