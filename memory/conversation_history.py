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

HONEST LIMITATION: WhatsApp/Telegram Desktop (pywinauto) still expose no
real per-message ID, only accessible text - so for those platforms,
duplicate detection stays a text-equality heuristic. Instagram rows CAN
carry a stable per-conversation `thread_id` (the DM thread URL - see
actions/browser_control.py's query_rows()), and every function here
accepts an optional `thread_id` that, when given, replaces the
contact-name text as the identity key entirely - sidestepping the
text-equality problem for that platform.

is_duplicate_incoming() narrows the remaining text-equality heuristic
with a time gate: matching text only counts as "the same still-unread
message" within `min_gap_seconds` of when it was first recorded. A
second, later message that happens to repeat the same words (e.g. "hi"
sent again an hour later) is no longer silently dropped - a real
tradeoff, not a bug, given what's actually available to read from the
screen for platforms with no stable ID.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from threading import Lock


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
HISTORY_PATH = BASE_DIR / "memory" / "conversation_history.json"

# Guards every read-modify-write as ONE operation, not just the write -
# append_turn() used to _load() (lock, release), mutate unlocked, then
# _save() (lock, release), a real TOCTOU race: two concurrent appends
# could both load the same starting state and the second _save() would
# silently discard the first's turn.
_lock = Lock()

MAX_TURNS_PER_THREAD = 20    # kept on disk per contact
MAX_TURNS_FOR_PROMPT = 6     # most recent turns actually fed to the AI brain

DEFAULT_DUPLICATE_WINDOW_SECONDS = 300
DEFAULT_REPLY_COOLDOWN_SECONDS = 15


def _key(platform: str, contact: str, thread_id: str | None = None) -> str:
    """A stable `thread_id` (e.g. Instagram's per-conversation URL) always
    wins over the contact-name text when given - see the module
    docstring."""
    platform = (platform or "").strip().lower()
    if thread_id:
        return f"{platform}::id::{thread_id.strip()}"
    return f"{platform}::{(contact or '').strip().lower()}"


def _load_unlocked() -> dict:
    if not HISTORY_PATH.exists():
        return {}
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[ConversationHistory] ⚠️ Load error: {e}")
        return {}


def _save_unlocked(data: dict) -> None:
    """Write to a temp file in the same directory, then os.replace() it
    over HISTORY_PATH - atomic on both POSIX and Windows for a
    same-filesystem rename, so a crash or a concurrent reader mid-write
    can never see a half-written/corrupt history file."""
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(HISTORY_PATH.parent), prefix=".conversation_history_", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, HISTORY_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"[ConversationHistory] ⚠️ Save error: {e}")


def _load() -> dict:
    with _lock:
        return _load_unlocked()


def _save(data: dict) -> None:
    with _lock:
        _save_unlocked(data)


def get_recent_turns(
    platform: str, contact: str, limit: int = MAX_TURNS_FOR_PROMPT, thread_id: str | None = None,
) -> list[dict]:
    """Up to `limit` most recent {role, text, ts} turns for this contact on
    this platform, oldest first. Empty if there's no history yet."""
    thread = _load().get(_key(platform, contact, thread_id), [])
    return thread[-limit:]


def append_turn(
    platform: str, contact: str, role: str, text: str, thread_id: str | None = None,
) -> None:
    """Appends one turn (role: 'them' or 'jarvis') to the contact's thread,
    trimming to MAX_TURNS_PER_THREAD. The whole read-modify-write happens
    under one lock acquisition - see the module-level `_lock` comment.
    Never raises."""
    if not text:
        return
    with _lock:
        data = _load_unlocked()
        key = _key(platform, contact, thread_id)
        thread = data.get(key, [])
        thread.append({"role": role, "text": text, "ts": time.time()})
        data[key] = thread[-MAX_TURNS_PER_THREAD:]
        _save_unlocked(data)


def _last_handled_incoming_turn(platform: str, contact: str, thread_id: str | None = None) -> dict | None:
    thread = _load().get(_key(platform, contact, thread_id), [])
    for turn in reversed(thread):
        if turn.get("role") == "them":
            return turn
    return None


def _last_handled_outgoing_turn(platform: str, contact: str, thread_id: str | None = None) -> dict | None:
    thread = _load().get(_key(platform, contact, thread_id), [])
    for turn in reversed(thread):
        if turn.get("role") == "jarvis":
            return turn
    return None


def replied_too_recently(
    platform: str,
    contact: str,
    thread_id: str | None = None,
    min_gap_seconds: float = DEFAULT_REPLY_COOLDOWN_SECONDS,
) -> bool:
    """True when JARVIS already sent an auto-reply to this contact/thread
    less than `min_gap_seconds` ago - regardless of what the new incoming
    text says. This is a different safety net than is_duplicate_incoming():
    that one only catches the SAME incoming text being answered twice; this
    one catches a runaway back-and-forth where each side (e.g. this feature
    talking to someone else's autoresponder) keeps generating DIFFERENT
    text, which text-equality dedup would never flag. A real human sending
    two genuine messages within the cooldown just waits a few extra seconds
    for the second reply - a small cost for not looping forever with
    another bot."""
    turn = _last_handled_outgoing_turn(platform, contact, thread_id)
    if turn is None:
        return False
    return (time.time() - turn.get("ts", 0)) < min_gap_seconds


def last_handled_incoming(platform: str, contact: str, thread_id: str | None = None) -> str:
    """The text of the last INCOMING ('them') message recorded for this
    contact - kept for callers that only need the text (e.g. building the
    AI prompt). For duplicate-skip decisions, prefer
    is_duplicate_incoming() instead, which also checks how long ago that
    was. Empty if none yet."""
    turn = _last_handled_incoming_turn(platform, contact, thread_id)
    return turn.get("text", "") if turn else ""


def is_duplicate_incoming(
    platform: str,
    contact: str,
    text: str,
    thread_id: str | None = None,
    min_gap_seconds: float = DEFAULT_DUPLICATE_WINDOW_SECONDS,
) -> bool:
    """True only when `text` matches the last incoming message recorded
    for this contact/thread AND it was recorded less than `min_gap_seconds`
    ago - "this is still the same unread row from a moment ago", not "this
    contact happened to say the same thing again later". See the module
    docstring for why this time gate exists and its own remaining
    limitation on platforms with no stable thread_id."""
    if not text:
        return False
    turn = _last_handled_incoming_turn(platform, contact, thread_id)
    if turn is None or turn.get("text", "") != text:
        return False
    age = time.time() - turn.get("ts", 0)
    return age < min_gap_seconds


def format_for_prompt(platform: str, contact: str, thread_id: str | None = None) -> str:
    """Recent turns formatted as plain conversation lines for the AI
    brain's prompt. Empty string when there's no history yet, so callers
    can skip adding an empty context section."""
    turns = get_recent_turns(platform, contact, thread_id=thread_id)
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
