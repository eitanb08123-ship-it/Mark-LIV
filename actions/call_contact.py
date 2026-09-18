"""
actions/call_contact.py — a real, free voice call, placed through WhatsApp,
Telegram, or Instagram's own calling feature (VoIP over your existing
internet connection) instead of a telephony provider (Twilio, etc.). No
phone number to buy, no per-minute cost, no card.

Reuses actions/send_message.py's already-working desktop/browser automation
(_open_app / _search_in_app / _open_browser_url / _paste_text / OS
detection) rather than duplicating it - opening the app and finding the
right chat is exactly the same problem send_message.py already solves.

WHO IT CAN CALL - enforced in code, not by asking the model nicely: this can
ONLY ever call the single contact name configured via
memory.config_manager.save_owner_contact_name(...), the same "hard-coded,
not prompt-based" rule core/self_improvement/safety_guard.py and
core/coding_agent/workspace.py already apply to their own boundaries. There
is deliberately no "receiver" parameter the model or a voice command could
fill in - unlike send_message (meant for messaging arbitrary contacts),
this tool exists specifically so JARVIS can call the phone's OWNER and
nobody else, so it takes no name at all as input.

HONESTY NOTE (this is the important part): none of WhatsApp/Telegram
Desktop or Instagram's web Direct Messages exposes a documented keyboard
shortcut or URL scheme for "start a voice call" - the call button is just
an icon in the chat header. This module reaches it by tabbing focus out of
the message box, the same best-effort approach actions/send_message.py
already uses for Instagram/Messenger's own unlabelled buttons. It is NOT
guaranteed to land on the right control on every version or window layout -
unlike open_app.py's launches, there is no reliable way to verify from here
that a call actually started (no "is a process now running" equivalent).
The returned message says so plainly instead of claiming false certainty.

DESKTOP-HIJACK NOTE, for anything that calls this on a timer rather than a
direct user command: every step here drives the real mouse/keyboard focus -
unlike core/self_improvement's or the coding agent's background work, which
only ever touch files/subprocesses, this can type into whatever window
happens to be focused if the user is doing something else at that moment.
windows_idle_seconds() below exists so a scheduler can skip a run while the
user is actively using the computer, rather than steal their keystrokes
mid-sentence. It is Windows-only (returns None elsewhere) - there is no
equivalent guard on macOS/Linux yet.
"""
from __future__ import annotations

import time

from actions.send_message import (
    _PYAUTOGUI,
    _open_app,
    _open_browser_url,
    _paste_text,
    _search_in_app,
)
from memory.config_manager import get_owner_contact_name

try:
    import pyautogui
except ImportError:
    pyautogui = None

_APP_NAMES = {
    "whatsapp": "WhatsApp",
    "wp": "WhatsApp",
    "wapp": "WhatsApp",
    "telegram": "Telegram",
    "tg": "Telegram",
}


def windows_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input, on Windows only. None on
    any other OS, or if the check itself fails - callers should treat None
    as "can't tell, proceed anyway" rather than "definitely idle"."""
    try:
        import ctypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        idle_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, idle_ms / 1000.0)
    except Exception:
        return None


def _call_via_desktop_app(app_name: str, receiver: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver, app_name)
    pyautogui.press("enter")
    time.sleep(1.2)

    # No documented shortcut/URL scheme starts a call in either app - the
    # call button is just an icon in the chat header. Tabbing backwards out
    # of the message box toward the header controls (Search / Call / Video /
    # Menu) is a best-effort guess at the right one, not a verified path.
    for _ in range(3):
        pyautogui.hotkey("shift", "tab")
        time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(1.0)

    return (
        f"Attempted to start a voice call to {receiver} via {app_name}. "
        f"I cannot confirm from here whether it actually connected - please "
        f"check your screen."
    )


def _call_via_instagram(receiver: str) -> str:
    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in the browser."

    _paste_text(receiver)
    time.sleep(1.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(0.4)
    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    # Now inside the chat thread - same "no documented shortcut" situation
    # as WhatsApp/Telegram above.
    for _ in range(3):
        pyautogui.hotkey("shift", "tab")
        time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(1.0)

    return (
        f"Attempted to start a voice call to {receiver} via Instagram. "
        f"I cannot confirm from here whether it actually connected - please "
        f"check your screen."
    )


def _call_via(platform: str, receiver: str) -> str:
    if platform in ("instagram", "ig", "insta"):
        return _call_via_instagram(receiver)
    return _call_via_desktop_app(_APP_NAMES.get(platform, "WhatsApp"), receiver)


def call_contact(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    platform = params.get("platform", "whatsapp").strip().lower()

    receiver = get_owner_contact_name()
    if not receiver:
        return (
            "No owner contact is configured yet, so I won't guess who to call. "
            "Set it once with: memory.config_manager.save_owner_contact_name("
            "'YourExactContactName')."
        )
    if not _PYAUTOGUI or pyautogui is None:
        return "PyAutoGUI is not installed - cannot control the desktop to place a call."

    if player:
        player.write_log(f"[call_contact] {platform} -> {receiver}")

    try:
        result = _call_via(platform, receiver)
    except Exception as e:
        result = f"Could not place the call: {e}"

    if player:
        player.write_log(f"[call_contact] {result}")

    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "call_contact",
    "description": (
        "Places a REAL voice call to the phone's OWNER (and only the owner - "
        "there is no way to name a different person) through WhatsApp, "
        "Telegram, or Instagram (VoIP over the internet - not the phone "
        "network, no cost, no phone number needed). Use when the user asks "
        "JARVIS to call them. This is best-effort desktop/browser automation "
        "(there is no official way to trigger it) - it cannot guarantee the "
        "call actually connected, only that it tried."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "platform": {
                "type": "STRING",
                "description": "WhatsApp, Telegram, or Instagram (default: WhatsApp).",
            },
        },
    },
    "handler": call_contact,
}
