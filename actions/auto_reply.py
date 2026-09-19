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
access via query_rows()/smart_click()/smart_type(), the same approach
actions/instagram_call_answer.py uses for incoming calls, and for the same
reason: browsers don't reliably expose a full accessibility tree the way
Electron desktop apps do, so pywinauto isn't an option here. Instagram's
actual inbox layout was equally never inspected live -
_find_unread_instagram_chats()'s row selector and its "dir=auto text
nodes separate a name from a preview" assumption are informed guesses,
not verified selectors (see query_rows()'s own docstring in
browser_control.py).

SUPPORTED: English and Hebrew UI text (every screenshot shared while
building this saw a Hebrew Windows/app UI). Not verified against any
specific WhatsApp/Telegram/Instagram version - see health_check().

Off by default. Turn on with
memory.config_manager.save_auto_reply_enabled(True). Which platform(s) -
any combination of whatsapp/telegram/instagram, watched simultaneously -
via memory.config_manager.save_auto_reply_platforms([...]).

Each reply is generated with the contact's recent conversation history
(memory/conversation_history.py) so a back-and-forth isn't treated as a
brand-new conversation on every message, and the same still-unread row
isn't answered twice on consecutive polls (conversation_history.
is_duplicate_incoming() - text equality plus a time gate; Instagram rows
additionally key by their stable thread URL instead of contact-name text
when one is available). A second, independent guard
(conversation_history.replied_too_recently()) caps how often this feature
will send to the SAME contact at all, regardless of what the incoming text
says - the safety net for a runaway back-and-forth with another
autoresponder, which text-equality dedup alone would never catch since
each side keeps generating different wording. Every generated reply also
passes through
_validate_reply() - a minimal safety/validation step (non-empty,
length-capped) between the AI brain and the sender, and every UI action
(click/type/press) is checked before the next one runs and before
anything is reported as sent - see _reply_via_instagram()/_reply_to_chat().
"""
from __future__ import annotations

import contextlib
import io
import random
import time

from core import gemini
from actions.send_message import _PYAUTOGUI, _ensure_foreground, _open_app, _paste_text, _search_in_app
from memory import conversation_history
from memory.config_manager import get_auto_reply_dry_run, get_auto_reply_enabled, get_auto_reply_platforms

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


def _focus_and_maximize(app_name: str) -> str:
    """Brings app_name's window to the foreground and maximizes it, ONCE,
    when Watch Mode is first turned on (see configure_auto_response()) - so
    the chat list/message view is actually visible on screen rather than
    running invisibly in the background. This does NOT run on every poll:
    the actual detection (_find_unread_chats()) reads the window's UI
    Automation tree directly and doesn't need real OS focus to do that, and
    _reply_to_chat() already re-establishes focus for itself right before
    typing (_ensure_foreground()) regardless of whatever the user is doing
    at that moment - repeatedly forcing focus here on every cycle would
    fight the user for their own window instead. Best-effort and never
    raises - a failure here does not prevent detection/replying from still
    working against a background/minimized window, this is purely for
    visibility."""
    if not _PYWINAUTO:
        return f"Could not bring {app_name} to the foreground: pywinauto is not installed."
    try:
        win = _connect(app_name)
        if win.is_minimized():
            win.restore()
        win.set_focus()
        win.maximize()
        return f"{app_name} window focused and maximized."
    except Exception as e:
        return f"Could not focus/maximize {app_name}: {e}"


_PRIVACY_NOTE = (
    "\n\n[PRIVACY NOTE: this dump can include real, visible chat content "
    "(sender names, message text) from the window's accessibility tree, "
    "not just structural metadata - only share it for calibrating this "
    "feature, not more broadly.]"
)


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
    return buf.getvalue()[:6000] + _PRIVACY_NOTE


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
        unread = [
            text for r in rows
            if (text := (r.window_text() or ""))
            and any(marker in text.lower() for marker in _UNREAD_MARKERS)
        ]
        # DIAGNOSTIC: the total row count is what tells "connected fine but
        # nothing looks unread" (a real 0) apart from "the pywinauto
        # connection/selector itself isn't finding the chat list at all"
        # (rows would be empty too) - both look the same from the caller's
        # side otherwise.
        print(f"[AutoReply] {app_name}: scanned {len(rows)} chat-list row(s), {len(unread)} look unread.")
        return unread
    except Exception as e:
        print(f"[AutoReply] Could not read {app_name}'s chat list: {e}")
        return []


def health_check(platform: str) -> str:
    """A lightweight structural probe: can this platform's detection find
    ANY control of the type it depends on at all (any ListItem in a
    WhatsApp/Telegram window, any conversation-row anchor on Instagram)?
    This does NOT confirm the selectors are calibrated correctly - only
    that the app is open and exposes something to look at, so a silently
    broken selector (an app update changed the UI, the window isn't
    logged in, etc.) is reported instead of the feature quietly doing
    nothing forever. Never raises."""
    platform = (platform or "").lower()
    if platform == "instagram":
        if not _BROWSER_CONTROL_AVAILABLE:
            return "unavailable: browser_control (playwright) is not installed."
        try:
            session = _get_browser_session()
            url = session.run(session.get_url(), timeout=15)
            if "/direct/inbox" not in (url or ""):
                session.run(session.go_to(_INSTAGRAM_INBOX_URL), timeout=30)
            rows = session.run(session.query_rows(_INSTAGRAM_ROW_SELECTOR), timeout=15)
        except Exception as e:
            return f"unavailable: could not read the Instagram inbox ({e})."
        return "ok" if rows else "warning: no conversation rows found at all - selectors may need calibration."

    app_name = platform.title()
    if not _PYWINAUTO:
        return "unavailable: pywinauto is not installed (Windows-only)."
    try:
        win = _connect(app_name)
        rows = win.descendants(control_type="ListItem")
    except Exception as e:
        return f"unavailable: could not connect to {app_name} ({e})."
    return "ok" if rows else f"warning: no chat-list rows found in {app_name} at all - selectors may need calibration."


def _get_browser_session(browser_name: str = "chrome"):
    return _registry.get(browser_name)


# Instagram DM rows are anchors linking to their own thread, e.g.
# /direct/t/17841400000000000/ - a stable per-conversation ID query_rows()
# can hand back, unlike flat page text. Still an informed guess (see module
# docstring), not a verified selector.
_INSTAGRAM_ROW_SELECTOR = 'a[href*="/direct/t/"]'


def _find_unread_instagram_chats(session) -> list[dict]:
    """Structural replacement for the old flat-get_text() heuristic:
    queries real inbox-row anchor elements (query_rows()) instead of
    guessing from flattened page text. Returns one dict per row that
    looks unread: {"contact": str, "incoming_text": str | None,
    "thread_id": str | None}. `incoming_text` is None - not a guess -
    whenever the row's own text nodes can't be confidently split into
    [name, real preview, unread/timestamp marker]; callers must skip
    replying in that case (see _reply_to_instagram_row()) rather than
    send whatever text happened to be there, which is the exact bug this
    replaces: sending "3 unread messages" to Gemini as if it were the
    contact's real message. Never raises; [] on any failure."""
    if not _BROWSER_CONTROL_AVAILABLE:
        return []
    try:
        url = session.run(session.get_url(), timeout=15)
        if "/direct/inbox" not in (url or ""):
            session.run(session.go_to(_INSTAGRAM_INBOX_URL), timeout=30)
        rows = session.run(session.query_rows(_INSTAGRAM_ROW_SELECTOR), timeout=15)
    except Exception as e:
        print(f"[AutoReply] Could not read Instagram's inbox: {e}")
        return []

    results = []
    for row in rows or []:
        texts = row.get("texts") or []
        if not any(any(marker in t.lower() for marker in _UNREAD_MARKERS) for t in texts):
            continue
        contact = texts[0] if texts else ""
        if not contact:
            continue
        incoming_text = texts[1] if len(texts) >= 3 else None
        results.append({
            "contact": contact,
            "incoming_text": incoming_text,
            "thread_id": row.get("href"),
        })
    # DIAGNOSTIC: same purpose as _find_unread_chats()'s scan count - tells
    # "connected fine, nothing unread" apart from "query_rows() itself
    # isn't finding the inbox rows at all".
    print(f"[AutoReply] Instagram: scanned {len(rows or [])} inbox row(s), {len(results)} look unread.")
    return results


def _reply_via_instagram(session, contact_hint: str, reply_text: str) -> str:
    """Checks the result of every action before moving to the next one,
    and re-reads the page afterward to confirm the message actually
    landed - a click/type/press can each individually "succeed" (Playwright
    found SOME matching element) while the message never reaches the
    conversation. Only a return starting with "Replied" means delivery
    was verified; callers must not save conversation history otherwise."""
    click_result = session.run(session.smart_click(contact_hint), timeout=10)
    if not click_result.startswith("Clicked"):
        return f"Could not open the conversation with {contact_hint}: {click_result}"

    type_result = session.run(session.smart_type("Message", reply_text), timeout=10)
    if not type_result.startswith("Typed"):
        return f"Could not find the message box for {contact_hint}: {type_result}"

    press_result = session.run(session.press("Enter"), timeout=10)
    if not press_result.startswith("Pressed"):
        return f"Could not submit the message to {contact_hint}: {press_result}"

    try:
        page_text = session.run(session.get_text(), timeout=10) or ""
    except Exception:
        page_text = ""
    if reply_text[:50] not in page_text:
        return (
            f"Sent to {contact_hint} but could not verify it actually appears in "
            f"the conversation - not saving to history."
        )

    return f"Replied to {contact_hint} via Instagram: {reply_text[:80]}"


def _split_row(row_text: str) -> tuple[str, str | None]:
    """A WhatsApp/Telegram chat-row's accessible text as (contact,
    incoming_text) - the contact is always assumed to lead the row (the same
    guess _find_unread_chats() makes). `incoming_text` is None - not a
    guess - whenever the row's structure doesn't look trustworthy enough to
    call the remainder "the real message": no second line at all, or the
    "contact" line itself contains an unread marker (meaning what we split
    on isn't actually a name). Mirrors the same "skip rather than guess"
    fix _find_unread_instagram_chats() already got for exactly this class
    of bug (sending a marker/badge string to Gemini as if it were the
    contact's real message) - callers must skip replying when this returns
    None, not send whatever text happened to be there."""
    contact, sep, rest = row_text.partition("\n")
    contact = contact.strip()
    rest = rest.strip()
    if not sep or not rest:
        return contact, None
    if any(marker in contact.lower() for marker in _UNREAD_MARKERS):
        return contact, None
    return contact, rest


def _reply_to_instagram_row(session, row: dict) -> str:
    contact = row.get("contact", "")
    if not contact:
        return "Could not determine who to reply to from an inbox row."
    thread_id = row.get("thread_id")
    incoming_text = row.get("incoming_text")

    if incoming_text is None:
        return (
            f"Could not confidently extract {contact}'s actual message from the "
            f"inbox row (only a name and an unread marker were found, no separate "
            f"preview text) - skipping rather than guessing."
        )

    if conversation_history.is_duplicate_incoming("instagram", contact, incoming_text, thread_id=thread_id):
        return f"Already replied to the latest message from {contact} - skipping duplicate."

    if conversation_history.replied_too_recently("instagram", contact, thread_id=thread_id):
        return (
            f"Already replied to {contact} within the last "
            f"{conversation_history.DEFAULT_REPLY_COOLDOWN_SECONDS:.0f}s - skipping to avoid a "
            f"runaway back-and-forth (e.g. with another autoresponder on their side)."
        )

    reply_text = _validate_reply(_generate_reply("instagram", contact, incoming_text))
    if not reply_text:
        return f"Gemini produced no reply for {contact} - nothing sent."

    if get_auto_reply_dry_run():
        conversation_history.append_turn("instagram", contact, "them", incoming_text, thread_id=thread_id)
        conversation_history.append_turn("instagram", contact, "jarvis", reply_text, thread_id=thread_id)
        return f"[DRY RUN] Would reply to {contact} via Instagram: {reply_text[:80]}"

    result = _reply_via_instagram(session, contact, reply_text)
    if not result.startswith("Replied"):
        return result

    conversation_history.append_turn("instagram", contact, "them", incoming_text, thread_id=thread_id)
    conversation_history.append_turn("instagram", contact, "jarvis", reply_text, thread_id=thread_id)
    return result


# A cap on how many chats get replied to in ONE poll cycle, and a small
# randomized pause between consecutive real sends within that cycle - both
# exist for the same reason: without them, coming back to (say) 15 unread
# chats after being away means the very next poll drives pyautogui through
# 15 back-to-back "open -> search -> paste -> send" sequences within about a
# minute, stealing keyboard/mouse focus repeatedly and firing 15 AI-authored
# messages before the user has any real chance to notice and disable the
# feature. The per-contact cooldown (replied_too_recently()) does nothing
# here since each contact in the burst is only being replied to once.
# Whatever doesn't fit in one cycle is simply left "unread" and picked up on
# the next poll - no separate queue needed.
_MAX_REPLIES_PER_CYCLE = 5
_MIN_DELAY_BETWEEN_REPLIES = 1.5
_MAX_DELAY_BETWEEN_REPLIES = 4.0


def _auto_reply_cycle_instagram() -> list[str]:
    if not _BROWSER_CONTROL_AVAILABLE:
        return ["auto_reply: browser_control (playwright) is not available."]

    session = _get_browser_session()
    results = []
    # The whole find -> reply sequence runs under one lock acquisition, so
    # instagram_call_answer.py's incoming-call check (or a plain
    # browser_control command) can't interleave mid-sequence and act on
    # whatever this just navigated/clicked to.
    with session.exclusive():
        rows = _find_unread_instagram_chats(session)
        batch = rows[:_MAX_REPLIES_PER_CYCLE]
        for i, row in enumerate(batch):
            try:
                result = _reply_to_instagram_row(session, row)
            except Exception as e:
                result = f"Could not reply to a chat: {e}"
            results.append(result)
            if result.startswith("Replied") and i < len(batch) - 1:
                time.sleep(random.uniform(_MIN_DELAY_BETWEEN_REPLIES, _MAX_DELAY_BETWEEN_REPLIES))
        deferred = len(rows) - len(batch)
        if deferred > 0:
            results.append(
                f"{deferred} more unread chat(s) found but deferred to the next poll "
                f"(per-cycle cap: {_MAX_REPLIES_PER_CYCLE}) - avoids sending a burst of "
                f"messages all at once."
            )
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


def _get_window_text(win, max_chars: int = 20000) -> str:
    """Best-effort flat dump of every descendant control's own text - the
    same class of check _reply_via_instagram() does via Playwright's
    get_text(), just via pywinauto's UI Automation tree instead of a DOM.
    Never raises; '' on any failure."""
    texts = []
    try:
        for ctrl in win.descendants():
            try:
                t = ctrl.window_text()
            except Exception:
                continue
            if t:
                texts.append(t)
    except Exception:
        pass
    return "\n".join(texts)[:max_chars]


def _reply_to_chat(app_name: str, chat_row_text: str) -> str:
    contact, incoming_text = _split_row(chat_row_text)
    if not contact:
        return "Could not determine who to reply to from the chat row's text."
    if incoming_text is None:
        return (
            f"Could not confidently extract {contact}'s actual message from the "
            f"chat row (the row had no separate line of message text to read) - "
            f"skipping rather than guessing."
        )
    platform = app_name.lower()

    if conversation_history.is_duplicate_incoming(platform, contact, incoming_text):
        return f"Already replied to the latest message from {contact} - skipping duplicate."

    if conversation_history.replied_too_recently(platform, contact):
        return (
            f"Already replied to {contact} within the last "
            f"{conversation_history.DEFAULT_REPLY_COOLDOWN_SECONDS:.0f}s - skipping to avoid a "
            f"runaway back-and-forth (e.g. with another autoresponder on their side)."
        )

    reply_text = _validate_reply(_generate_reply(platform, contact, incoming_text))
    if not reply_text:
        return f"Gemini produced no reply for {contact} - nothing sent."

    if get_auto_reply_dry_run():
        conversation_history.append_turn(platform, contact, "them", incoming_text)
        conversation_history.append_turn(platform, contact, "jarvis", reply_text)
        return f"[DRY RUN] Would reply to {contact} via {app_name}: {reply_text[:80]}"

    if not _open_app(app_name):
        return f"Could not open {app_name}."
    if _ensure_foreground(app_name) is False:
        return (
            f"Could not confirm {app_name} has keyboard focus - not sending, to avoid "
            f"typing this reply into whatever window actually had focus."
        )
    time.sleep(1.0)
    _search_in_app(contact, app_name)
    pyautogui.press("enter")
    time.sleep(0.8)
    _paste_text(reply_text)
    pyautogui.press("enter")
    time.sleep(0.3)

    try:
        window_text = _get_window_text(_connect(app_name))
    except Exception:
        window_text = ""
    if reply_text[:50] not in window_text:
        return (
            f"Sent to {contact} via {app_name} but could not verify it actually "
            f"appears in the conversation - not saving to history."
        )

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
    batch = unread[:_MAX_REPLIES_PER_CYCLE]
    results = []
    for i, row_text in enumerate(batch):
        try:
            result = _reply_to_chat(app_name, row_text)
        except Exception as e:
            result = f"Could not reply to a chat: {e}"
        results.append(result)
        if result.startswith("Replied") and i < len(batch) - 1:
            time.sleep(random.uniform(_MIN_DELAY_BETWEEN_REPLIES, _MAX_DELAY_BETWEEN_REPLIES))
    deferred = len(unread) - len(batch)
    if deferred > 0:
        results.append(
            f"{deferred} more unread chat(s) found but deferred to the next poll "
            f"(per-cycle cap: {_MAX_REPLIES_PER_CYCLE}) - avoids sending a burst of "
            f"messages all at once."
        )
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
