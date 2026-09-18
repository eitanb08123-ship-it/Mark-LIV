"""
actions/auto_reply.py — EXPERIMENTAL: automatically replies to incoming
WhatsApp/Telegram messages with a Gemini-generated response, sent
immediately with no human review. That "send immediately" behavior was an
explicit, informed choice: the risk (an AI-written reply goes out under the
user's identity, to whoever messaged, with no way to unsend it) was raised
and the user chose speed over a confirm.py-gated review step anyway.

WHY THIS IS EXPERIMENTAL, HONESTLY - read before relying on it: every other
action in this project that drives the desktop clicks through a small,
fixed keyboard sequence (open app, search, Enter) - it never needs to know
what's already on screen. This module is different in kind: it has to read
the chat app's actual structure to notice "a new message arrived, from
whom." For WhatsApp/Telegram Desktop that means UI Automation (pywinauto,
backend "uia" - the same pattern actions/game_updater.py already uses for
Steam's installer dialog), which was never inspected against a real
running instance while writing this - _find_unread_chats()'s "match rows
whose accessible text contains 'unread'" is a first guess based on
WhatsApp Web's own English aria-labels, not a verified selector, and will
likely need correction (use inspect_chat_window() below). For Instagram
(browser-based, no desktop app) this instead reuses
actions/browser_control.py's persistent Playwright session - real DOM
access via smart_click()/smart_type(), the same approach
actions/instagram_call_answer.py uses for incoming calls, and for the same
reason: browsers don't reliably expose a full accessibility tree the way
Electron desktop apps do, so pywinauto isn't an option here. Instagram's
actual inbox layout was equally never inspected live - _find_unread_instagram_chats()'s
"rows mentioning 'unread'" is the same kind of first guess.

Off by default. Turn on with
memory.config_manager.save_auto_reply_enabled(True). Which platform(s) -
any combination of whatsapp/telegram/instagram, watched simultaneously -
via memory.config_manager.save_auto_reply_platforms([...]).

Each reply is generated with the contact's recent conversation history
(memory/conversation_history.py) so a back-and-forth isn't treated as a
brand-new conversation on every message, and the same still-unread row
isn't answered twice on consecutive polls (a text-equality heuristic - see
that module's docstring for its known tradeoff). Every generated reply
also passes through _validate_reply() - a minimal safety/validation step
(non-empty, length-capped) between the AI brain and the sender.
"""
from __future__ import annotations

import contextlib
import io
import time

from core import gemini
from actions.send_message import _PYAUTOGUI, _open_app, _paste_text, _search_in_app
from memory import conversation_history
from memory.config_manager import get_auto_reply_enabled, get_auto_reply_platforms

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    from pywinauto import Application
    _PYWINAUTO = True
except ImportError:
    _PYWINAUTO = False
    Application = None

try:
    from actions.browser_control import _registry
    _BROWSER_CONTROL_AVAILABLE = True
except ImportError:
    _registry = None
    _BROWSER_CONTROL_AVAILABLE = False

_WINDOW_TITLE_PATTERNS = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
}

_INSTAGRAM_INBOX_URL = "https://www.instagram.com/direct/inbox/"


def _connect(app_name: str, timeout: float = 5.0):
    """Connects to the already-open app window via UI Automation. Raises on
    failure - callers decide how to report that."""
    pattern = _WINDOW_TITLE_PATTERNS.get(app_name.lower(), app_name)
    app = Application(backend="uia").connect(title_re=f".*{pattern}.*", timeout=timeout)
    return app.top_window()


def inspect_chat_window(app_name: str = "WhatsApp", max_depth: int = 3) -> str:
    """DIAGNOSTIC, not used by the auto-reply loop itself. Connects to
    `app_name`'s window (it must already be open) and returns pywinauto's
    own control-identifiers dump - run this and share the output so
    _find_unread_chats()'s matching can be corrected against what your
    actual WhatsApp/Telegram window's accessibility tree looks like,
    instead of the English-aria-label guess it starts with."""
    if not _PYWINAUTO:
        return "pywinauto is not installed (Windows-only)."
    try:
        win = _connect(app_name)
    except Exception as e:
        return f"Could not connect to {app_name} (is it open?): {e}"

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            win.print_control_identifiers(depth=max_depth)
    except Exception as e:
        return f"Connected to {app_name}, but could not dump its UI tree: {e}"
    return buf.getvalue()[:6000]


# English AND Hebrew markers: every screenshot shared while building this
# feature showed a Hebrew Windows/app UI, so an English-only match would
# silently never fire regardless of anything else being right. Still a
# guess (see module docstring) - just one accounting for the one concrete,
# well-justified thing known about the real setup.
_UNREAD_MARKERS = ("unread", "שלא נקראה", "שלא נקראו", "לא נקראה", "לא נקראו")


def _find_unread_chats(app_name: str) -> list[str]:
    """Best-effort, uncalibrated (see module docstring): returns the full
    accessible text of each chat-list row that mentions an unread marker
    (English or Hebrew - see _UNREAD_MARKERS). Empty on any failure - never
    raises, since this runs unattended on a timer."""
    if not _PYWINAUTO:
        return []
    try:
        win = _connect(app_name)
        rows = win.descendants(control_type="ListItem")
        return [
            text for r in rows
            if (text := (r.window_text() or ""))
            and any(marker in text.lower() for marker in _UNREAD_MARKERS)
        ]
    except Exception as e:
        print(f"[AutoReply] Could not read {app_name}'s chat list: {e}")
        return []


def _get_browser_session(browser_name: str = "chrome"):
    return _registry.get(browser_name)


def _find_unread_instagram_chats() -> list[str]:
    """Best-effort, uncalibrated (see module docstring): navigates the
    shared Playwright session to Instagram's inbox if it isn't already
    there, then scans the flat page text for lines mentioning an unread
    marker (English or Hebrew - see _UNREAD_MARKERS) and pairs each with the
    line right before it (the contact name, in a typical inbox row's
    reading order) - get_text() only returns the whole page's inner_text()
    with no row boundaries, so this is a heuristic, not a structural match
    the way _find_unread_chats()'s pywinauto ListItems are. Returns
    "<contact>\\n<unread line>" per match, matching the row format
    _reply_via_instagram()'s caller expects. Never raises."""
    if not _BROWSER_CONTROL_AVAILABLE:
        return []
    try:
        session = _get_browser_session()
        url = session.run(session.get_url(), timeout=15)
        if "instagram.com" not in (url or ""):
            session.run(session.go_to(_INSTAGRAM_INBOX_URL), timeout=30)
        text = session.run(session.get_text(), timeout=15)
    except Exception as e:
        print(f"[AutoReply] Could not read Instagram's inbox: {e}")
        return []

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    results = []
    for i, line in enumerate(lines):
        if any(marker in line.lower() for marker in _UNREAD_MARKERS):
            contact = lines[i - 1] if i > 0 else ""
            results.append(f"{contact}\n{line}")
    return results


def _reply_via_instagram(contact_hint: str, reply_text: str) -> str:
    session = _get_browser_session()
    session.run(session.smart_click(contact_hint), timeout=10)
    session.run(session.smart_type("Message", reply_text), timeout=10)
    session.run(session.press("Enter"), timeout=10)
    return f"Replied to {contact_hint} via Instagram: {reply_text[:80]}"


def _split_row(row_text: str) -> tuple[str, str]:
    """A chat-row's accessible text as (contact, rest-of-row) - the
    contact is always assumed to lead the row (the same guess
    _find_unread_chats()/_find_unread_instagram_chats() make); the
    remainder is treated as the incoming message content for history/dedup
    purposes, uncalibrated as noted in the module docstring."""
    contact, _, rest = row_text.partition("\n")
    return contact.strip(), rest.strip()


def _reply_to_instagram_row(row_text: str) -> str:
    contact, incoming_text = _split_row(row_text)
    if not contact:
        return "Could not determine who to reply to from an inbox row."

    if incoming_text and incoming_text == conversation_history.last_handled_incoming("instagram", contact):
        return f"Already replied to the latest message from {contact} - skipping duplicate."

    reply_text = _validate_reply(_generate_reply("instagram", contact, incoming_text))
    if not reply_text:
        return f"Gemini produced no reply for {contact} - nothing sent."

    result = _reply_via_instagram(contact, reply_text)

    conversation_history.append_turn("instagram", contact, "them", incoming_text)
    conversation_history.append_turn("instagram", contact, "jarvis", reply_text)
    return result


def _auto_reply_cycle_instagram() -> list[str]:
    if not _BROWSER_CONTROL_AVAILABLE:
        return ["auto_reply: browser_control (playwright) is not available."]

    results = []
    for row_text in _find_unread_instagram_chats():
        try:
            results.append(_reply_to_instagram_row(row_text))
        except Exception as e:
            results.append(f"Could not reply to a chat: {e}")
    return results


_MAX_REPLY_CHARS = 1000


def _validate_reply(text: str) -> str:
    """Minimal Response Safety/Validation step between the AI brain and
    the sender: strips whitespace, rejects empty output, and caps length
    so a runaway generation can't send an enormous message. Returns ''
    when the reply should not be sent."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    return cleaned[:_MAX_REPLY_CHARS]


def _generate_reply(platform: str, contact: str, incoming_text: str) -> str:
    history = conversation_history.format_for_prompt(platform, contact)
    history_block = (
        f"\n\nConversation so far with this contact (most recent last) - "
        f"stay consistent with it, this is NOT a new conversation:\n{history}"
        if history else ""
    )
    prompt = (
        "You are replying to an incoming chat message on behalf of the "
        "phone's owner, who is away from their device. Reply naturally and "
        "briefly (one or two short sentences), in the same language as the "
        "incoming message. Do not mention that you are an AI."
        f"{history_block}\n\n"
        f"New incoming message:\n{incoming_text}"
    )
    return gemini.text(prompt, tier=gemini.FAST, timeout_ms=15000, default="").strip()


def _reply_to_chat(app_name: str, chat_row_text: str) -> str:
    contact, incoming_text = _split_row(chat_row_text)
    if not contact:
        return "Could not determine who to reply to from the chat row's text."
    platform = app_name.lower()

    if incoming_text and incoming_text == conversation_history.last_handled_incoming(platform, contact):
        return f"Already replied to the latest message from {contact} - skipping duplicate."

    reply_text = _validate_reply(_generate_reply(platform, contact, incoming_text))
    if not reply_text:
        return f"Gemini produced no reply for {contact} - nothing sent."

    if not _open_app(app_name):
        return f"Could not open {app_name}."
    time.sleep(1.0)
    _search_in_app(contact, app_name)
    pyautogui.press("enter")
    time.sleep(0.8)
    _paste_text(reply_text)
    pyautogui.press("enter")
    time.sleep(0.3)

    conversation_history.append_turn(platform, contact, "them", incoming_text)
    conversation_history.append_turn(platform, contact, "jarvis", reply_text)

    return f"Replied to {contact} via {app_name}: {reply_text[:80]}"


def _auto_reply_cycle_desktop_app(platform: str) -> list[str]:
    if not _PYAUTOGUI or pyautogui is None:
        return ["auto_reply: PyAutoGUI is not installed."]
    if not _PYWINAUTO:
        return ["auto_reply: pywinauto is not installed (Windows-only feature right now)."]

    app_name = platform.title()
    unread = _find_unread_chats(app_name)
    results = []
    for row_text in unread:
        try:
            results.append(_reply_to_chat(app_name, row_text))
        except Exception as e:
            results.append(f"Could not reply to a chat: {e}")
    return results


def auto_reply_cycle(platforms: list[str] | str | None = None) -> list[str]:
    """One pass across every enabled platform (by default, all of
    memory.config_manager.get_auto_reply_platforms() - e.g. WhatsApp AND
    Instagram at once, not one at a time): find unread chats, generate and
    send a reply to each. Returns one result string per chat found across
    all platforms (empty list if none, or if the feature is disabled) -
    main.py's background loop logs each one. Never raises.

    `platforms` accepts a single platform name (kept for direct/manual
    calls and existing tests) or a list; None uses the configured set."""
    if not get_auto_reply_enabled():
        return []

    if platforms is None:
        platform_list = get_auto_reply_platforms()
    elif isinstance(platforms, str):
        platform_list = [platforms]
    else:
        platform_list = list(platforms)

    results = []
    for platform in platform_list:
        platform = platform.lower()
        if platform == "instagram":
            results.extend(_auto_reply_cycle_instagram())
        else:
            results.extend(_auto_reply_cycle_desktop_app(platform))
    return results


def _inspect_chat_window_action(parameters: dict, player=None, **_) -> str:
    app_name = (parameters or {}).get("app_name", "WhatsApp").strip() or "WhatsApp"
    result = inspect_chat_window(app_name)
    if player:
        player.write_log(f"[InspectChatUI] dumped {len(result)} chars for {app_name} - see console/logs.")
    print(result)
    return (
        f"Dumped {app_name}'s UI tree to the console/log (first 6000 chars). "
        f"Copy it from there and share it so auto-reply's chat detection can "
        f"be calibrated to what your window actually looks like."
    )


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "inspect_chat_window",
    "description": (
        "DIAGNOSTIC/DEVELOPER tool: dumps the UI Automation tree of an "
        "already-open WhatsApp or Telegram Desktop window to the console, "
        "so the auto-reply feature's unread-message detection can be "
        "calibrated against what the real window looks like. Not part of "
        "normal conversation - only use when explicitly asked to inspect "
        "or debug the chat app's window structure."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "WhatsApp or Telegram (default: WhatsApp). The app must already be open.",
            },
        },
    },
    "handler": _inspect_chat_window_action,
}
