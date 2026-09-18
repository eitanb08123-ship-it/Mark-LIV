"""
memory/conversation_history.py — per-contact conversation threads for the
auto-reply feature (actions/auto_reply.py), so a reply is generated with
the last few turns of context instead of treating every incoming message
as a brand-new conversation, and so the same still-unread row on the next
poll doesn't get answered twice.

Deliberately separate from memory_manager.py's long_term.json: that store
holds durable facts about the user (identity/preferences/projects/...) and
is pasted into the system prompt every session - a running WhatsApp/
Instagram chat log doesn't belong there and would bloat every prompt.

Keyed by (platform, contact) so the same contact name on two platforms
never shares a thread, and several contacts on the same platform never
collide - this is what lets auto_reply_cycle() hold multiple concurrent
conversations correctly.

HONEST LIMITATION: there is no real per-message ID available from either
automation path (pywinauto's UI Automation tree and Playwright's page text
both expose accessible text, not stable message identifiers), so
last_handled_incoming()'s duplicate check is a text-equality heuristic: if
the exact same incoming text was the last thing recorded for a contact, a
new poll is treated as "still the same unread message, already answered"
and skipped. A genuinely new message that happens to repeat the exact same
text (e.g. "hi" sent twice) would also be skipped - a real tradeoff, not a
bug, given what's actually available to read from the screen.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from threading import Lock


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
HISTORY_PATH = BASE_DIR / "memory" / "conversation_history.json"
_lock = Lock()

MAX_TURNS_PER_THREAD = 20    # kept on disk per contact
MAX_TURNS_FOR_PROMPT = 6     # most recent turns actually fed to the AI brain


def _key(platform: str, contact: str) -> str:
    return f"{(platform or '').strip().lower()}::{(contact or '').strip().lower()}"


def _load() -> dict:
    if not HISTORY_PATH.exists():
        return {}
    with _lock:
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[ConversationHistory] ⚠️ Load error: {e}")
            return {}


def _save(data: dict) -> None:
    with _lock:
        try:
            HISTORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[ConversationHistory] ⚠️ Save error: {e}")


def get_recent_turns(platform: str, contact: str, limit: int = MAX_TURNS_FOR_PROMPT) -> list[dict]:
    """Up to `limit` most recent {role, text, ts} turns for this contact on
    this platform, oldest first. Empty if there's no history yet."""
    thread = _load().get(_key(platform, contact), [])
    return thread[-limit:]


def append_turn(platform: str, contact: str, role: str, text: str) -> None:
    """Appends one turn (role: 'them' or 'jarvis') to the contact's thread,
    trimming to MAX_TURNS_PER_THREAD. Never raises."""
    if not text:
        return
    data = _load()
    key = _key(platform, contact)
    thread = data.get(key, [])
    thread.append({"role": role, "text": text, "ts": time.time()})
    data[key] = thread[-MAX_TURNS_PER_THREAD:]
    _save(data)


def last_handled_incoming(platform: str, contact: str) -> str:
    """The text of the last INCOMING ('them') message recorded for this
    contact - used to detect and skip answering the same still-unread row
    twice on consecutive polls. Empty if none yet. See the module
    docstring for this heuristic's known tradeoff."""
    thread = _load().get(_key(platform, contact), [])
    for turn in reversed(thread):
        if turn.get("role") == "them":
            return turn.get("text", "")
    return ""


def format_for_prompt(platform: str, contact: str) -> str:
    """Recent turns formatted as plain conversation lines for the AI
    brain's prompt. Empty string when there's no history yet, so callers
    can skip adding an empty context section."""
    turns = get_recent_turns(platform, contact)
    if not turns:
        return ""
    lines = []
    for turn in turns:
        speaker = "Them" if turn.get("role") == "them" else "You (JARVIS)"
        lines.append(f"{speaker}: {turn.get('text', '')}")
    return "\n".join(lines)


def active_conversations() -> list[dict]:
    """One summary row per contact thread - platform, contact, how many
    turns are kept, and the last turn's role/text/timestamp. For the
    dashboard's Communications view. Never raises."""
    data = _load()
    out = []
    for key, thread in data.items():
        if not thread:
            continue
        platform, _, contact = key.partition("::")
        last = thread[-1]
        out.append({
            "platform": platform,
            "contact": contact,
            "turns": len(thread),
            "last_role": last.get("role", ""),
            "last_text": (last.get("text", "") or "")[:200],
            "last_ts": last.get("ts", 0),
        })
    return out
