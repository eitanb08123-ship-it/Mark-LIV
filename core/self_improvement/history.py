"""
core/self_improvement/history.py — a durable, append-only-by-default log of
every self-improvement attempt: successful, failed, rolled back, or blocked.

WHY IT EXISTS
    Without this, the engine has no memory of its own past attempts and can
    retry the exact same failed solution to the same problem forever. With
    it, the planner is told what was already tried and what happened, so a
    second attempt at the same problem has to bring something new.

    It is also the human's audit trail: every write the engine ever made to
    the actual codebase traces back to one entry here, with the branch name,
    the files touched, and why it was judged to have passed or failed.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from memory.config_manager import get_base_dir

_HISTORY_PATH = get_base_dir() / "memory" / "self_improvement_history.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    if not _HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[SelfImprovement] history file unreadable, starting fresh: {e}")
        return []


def _save(entries: list[dict]) -> None:
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def record(entry: dict) -> str:
    """Appends one attempt and returns its id. Never raises - a broken
    history write must not take down an improvement attempt that otherwise
    succeeded or failed cleanly."""
    entry = dict(entry)
    entry.setdefault("id", str(uuid.uuid4())[:8])
    entry.setdefault("timestamp", time.time())
    try:
        with _lock:
            entries = _load()
            entries.append(entry)
            _save(entries)
    except Exception as e:
        print(f"[SelfImprovement] history write failed: {e}")
    return entry["id"]


def update_status(entry_id: str, status: str, extra: dict | None = None) -> bool:
    """Used when a pending 'awaiting_approval' entry is later approved or
    rejected. Returns False (and changes nothing) if no entry with that id
    exists, rather than silently creating one."""
    try:
        with _lock:
            entries = _load()
            for entry in entries:
                if entry.get("id") == entry_id:
                    entry["status"] = status
                    entry["resolved_at"] = time.time()
                    if extra:
                        entry.update(extra)
                    _save(entries)
                    return True
    except Exception as e:
        print(f"[SelfImprovement] history update failed: {e}")
    return False


def get(entry_id: str) -> dict | None:
    for entry in _load():
        if entry.get("id") == entry_id:
            return entry
    return None


def all_entries() -> list[dict]:
    with _lock:
        return _load()


def recent_failed_solutions(problem_signature: str) -> list[dict]:
    """Every past attempt at the same problem signature that did not
    succeed - so the planner can be told what was already tried instead of
    proposing it again with no new information."""
    return [
        e for e in all_entries()
        if e.get("problem_signature") == problem_signature and e.get("status") not in ("success",)
    ]
