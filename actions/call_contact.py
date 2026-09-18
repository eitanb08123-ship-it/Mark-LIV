"""
actions/call_contact.py — a real, free voice call, placed through WhatsApp or
Telegram Desktop's own calling feature (VoIP over your existing internet
connection) instead of a telephony provider (Twilio, etc.). No phone number
to buy, no per-minute cost, no card.

Reuses actions/send_message.py's already-working desktop automation
(_open_app / _search_in_app / OS detection) rather than duplicating it -
opening the app and finding the right chat is exactly the same problem
send_message.py already solves.

HONESTY NOTE (this is the important part): neither WhatsApp nor Telegram
Desktop exposes a documented keyboard shortcut or URL scheme for "start a
voice call" - the call button is just an icon in the chat header. This
module reaches it by tabbing focus out of the message box, the same
best-effort approach actions/send_message.py already uses for Instagram/
Messenger's own unlabelled buttons. It is NOT guaranteed to land on the
right control on every WhatsApp/Telegram version or window layout - unlike
open_app.py's launches, there is no reliable way to verify from here that a
call actually started (no "is a process now running" equivalent). The
returned message says so plainly instead of claiming false certainty.
"""
from __future__ import annotations

import time

from actions.send_message import _PYAUTOGUI, _open_app, _search_in_app

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


def _call_via_chat(app_name: str, receiver: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver)
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


def call_contact(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    receiver = params.get("receiver", "").strip()
    platform = params.get("platform", "whatsapp").strip().lower()

    if not receiver:
        return "Please specify who to call."
    if not _PYAUTOGUI or pyautogui is None:
        return "PyAutoGUI is not installed - cannot control the desktop to place a call."

    app_name = _APP_NAMES.get(platform, "WhatsApp")

    if player:
        player.write_log(f"[call_contact] {app_name} -> {receiver}")

    try:
        result = _call_via_chat(app_name, receiver)
    except Exception as e:
        result = f"Could not place the call: {e}"

    if player:
        player.write_log(f"[call_contact] {result}")

    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "call_contact",
    "description": (
        "Places a REAL voice call to a contact through WhatsApp or Telegram "
        "Desktop (VoIP over the internet - not the phone network, no cost, "
        "no phone number needed). Use when the user asks JARVIS to call "
        "them or someone else. This is best-effort desktop automation "
        "(there is no official way to trigger it) - it cannot guarantee the "
        "call actually connected, only that it tried."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Contact name to call, exactly as saved in the app (e.g. the user's own saved contact name for themselves).",
            },
            "platform": {
                "type": "STRING",
                "description": "WhatsApp or Telegram (default: WhatsApp).",
            },
        },
        "required": ["receiver"],
    },
    "handler": call_contact,
}
