"""
actions/self_improvement_resolve.py — explicitly approve or reject a
pending self-improvement attempt by id, independent of whether its
on-screen confirmation banner (from self_improve.py) is still showing.
"""
from __future__ import annotations

from core.self_improvement import approve, reject


def self_improvement_resolve(parameters: dict, player=None) -> str:
    entry_id = parameters.get("id", "").strip()
    decision = parameters.get("decision", "").strip().lower()

    if not entry_id:
        return "I need the id of the attempt to resolve - check self_improvement_status."
    if decision not in ("approve", "reject"):
        return "Decision must be 'approve' or 'reject'."

    result = approve(entry_id) if decision == "approve" else reject(entry_id)

    if player:
        try:
            player.write_log(f"[SELF-IMPROVEMENT] {decision.upper()} ({entry_id}): {result.get('reason', '')}")
        except Exception:
            pass
    return result.get("reason", result.get("status", "Done."))


TOOL = {
    "name": "self_improvement_resolve",
    "description": (
        "Approve or reject a specific pending self-improvement attempt by its "
        "id (from self_improvement_status). Use when the user refers back to a "
        "previously-shown attempt, e.g. 'approve that improvement' or 'reject "
        "attempt abc123'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING", "description": "The attempt's id, e.g. 'abc123'."},
            "decision": {"type": "STRING", "description": "'approve' or 'reject'."},
        },
        "required": ["id", "decision"],
    },
    "handler": self_improvement_resolve,
}
