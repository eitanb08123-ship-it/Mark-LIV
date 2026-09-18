"""
actions/whatsapp_call_answer.py — EXPERIMENTAL: auto-accepts an incoming
WhatsApp Desktop voice/video call.

Reuses actions/auto_reply.py's pywinauto connection helper (_connect) - the
same UI-Automation approach already used there to detect unread messages,
applied here to detect an incoming-call banner instead. See
actions/instagram_call_answer.py for the browser/Playwright equivalent used
for Instagram, where a real DOM is available; WhatsApp Desktop has no such
thing, so this - like actions/auto_reply.py's WhatsApp path - depends on
pywinauto's UI Automation tree instead, which IS a real click
(win32 SendInput-backed, via click_input()) on the user's actual desktop,
unlike Instagram's Playwright-dispatched clicks.

HONESTY NOTE: WhatsApp's incoming-call UI text/labels were never inspected
live while writing this (no access to the user's machine from here).
_CALL_INDICATOR_TEXTS and _ACCEPT_LABELS are English-language guesses -
calibrate with actions.auto_reply.inspect_chat_window("WhatsApp") pointed
at the window while a real call is ringing.

Off by default. Turn on with
memory.config_manager.save_whatsapp_call_answer_enabled(True).
"""
from __future__ import annotations

from actions.auto_reply import _PYWINAUTO, _connect
from memory.config_manager import get_whatsapp_call_answer_enabled

_CALL_INDICATOR_TEXTS = ("is calling", "incoming call", "video call", "voice call")
_ACCEPT_LABELS = ("Accept", "Answer")


def check_and_answer(app_name: str = "WhatsApp") -> str:
    """One poll: reads the window's visible text, and if it looks like a
    call is ringing, looks for a button matching one of the known accept
    labels and clicks it. Returns '' when nothing is ringing (the common
    case) - never raises."""
    if not _PYWINAUTO:
        return "pywinauto is not installed (Windows-only)."

    try:
        win = _connect(app_name)
        all_texts = [(el.window_text() or "") for el in win.descendants()]
    except Exception as e:
        return f"Could not read {app_name}'s window: {e}"

    combined = " ".join(all_texts).lower()
    if not any(marker in combined for marker in _CALL_INDICATOR_TEXTS):
        return ""

    for label in _ACCEPT_LABELS:
        try:
            buttons = [
                b for b in win.descendants(control_type="Button")
                if (b.window_text() or "").strip().lower() == label.lower()
            ]
        except Exception:
            continue
        if buttons:
            try:
                buttons[0].click_input()
                return f"Answered an incoming {app_name} call (matched '{label}')."
            except Exception as e:
                return f"Found an accept button ('{label}') but could not click it: {e}"

    return (
        f"Something on {app_name}'s window looked like an incoming call, but no "
        f"accept button could be found - this needs calibration (see the module "
        f"docstring)."
    )


def run_cycle(app_name: str = "WhatsApp") -> str:
    """Full cycle for the background poller (main.py). Returns '' when
    nothing happened (feature disabled, or nothing ringing) - callers
    should only log a non-empty result."""
    if not get_whatsapp_call_answer_enabled():
        return ""
    return check_and_answer(app_name)
