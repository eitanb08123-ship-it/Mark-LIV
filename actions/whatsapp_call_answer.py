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
_CALL_INDICATOR_TEXTS and _ACCEPT_LABELS include both English and Hebrew
guesses (every screenshot shared while building this feature showed a
Hebrew Windows/app UI) - still guesses, not verified selectors. Calibrate
with actions.auto_reply.inspect_chat_window("WhatsApp") pointed at the
window while a real call is ringing.

Detection is scoped to ONE container (_find_call_dialog()): a call
indicator and an accept button only count together when they're
descendants of the SAME Pane/Group/Dialog/Window element, never a text
hit in one part of the window paired with a same-named button from an
unrelated part (an old "calling you later" chat message elsewhere, plus
some other Accept button, must never be mistaken for a ringing call).

SUPPORTED: English and Hebrew UI text. Not verified against any specific
WhatsApp Desktop version.

Off by default. Turn on with
memory.config_manager.save_whatsapp_call_answer_enabled(True).
"""
from __future__ import annotations

from actions.auto_reply import _PYWINAUTO, _connect
from memory.config_manager import get_whatsapp_call_answer_enabled

_CALL_INDICATOR_TEXTS = (
    "is calling", "incoming call", "video call", "voice call",
    "מתקשר", "מתקשרת", "שיחה נכנסת", "שיחת וידאו", "שיחה קולית",
)
_ACCEPT_LABELS = ("Accept", "Answer", "קבל", "ענה", "ענה לשיחה")

# A call banner/dialog is one of these container types - checking each
# container's OWN descendants (not the whole window at once) is what lets
# a text match and a button match be tied to the same visual area. Still
# broader per-container than ideal (each container's own descendants()
# call is unfiltered), but far narrower than scanning the whole window's
# controls against the whole window's buttons with no relationship at all.
_CONTAINER_TYPES = ("Pane", "Group", "Dialog", "Window")


def _find_call_dialog(win):
    """Returns (button, label, True) for the first container that has
    both a call-indicator text AND a matching accept-button label among
    its OWN descendants; (None, None, True) if some container's text
    matched but no button did (still worth reporting, so
    check_and_answer() can say "needs calibration" rather than staying
    silent); (None, None, False) if nothing matched anywhere. Never
    raises - errors on one container just move on to the next."""
    any_text_matched = False
    for control_type in _CONTAINER_TYPES:
        try:
            containers = win.descendants(control_type=control_type)
        except Exception:
            continue
        for container in containers:
            try:
                texts = " ".join((c.window_text() or "") for c in container.descendants()).lower()
            except Exception:
                continue
            if not any(marker in texts for marker in _CALL_INDICATOR_TEXTS):
                continue
            any_text_matched = True
            try:
                buttons = container.descendants(control_type="Button")
            except Exception:
                continue
            for label in _ACCEPT_LABELS:
                matches = [b for b in buttons if (b.window_text() or "").strip().lower() == label.lower()]
                if matches:
                    return matches[0], label, True
    return None, None, any_text_matched


def check_and_answer(app_name: str = "WhatsApp") -> str:
    """One poll: looks for a call-indicator text and an accept button
    scoped to the SAME container (_find_call_dialog()), and clicks it if
    found. Returns '' when nothing is ringing (the common case) - never
    raises."""
    if not _PYWINAUTO:
        return "pywinauto is not installed (Windows-only)."

    try:
        win = _connect(app_name)
    except Exception as e:
        return f"Could not read {app_name}'s window: {e}"

    try:
        button, label, any_text_matched = _find_call_dialog(win)
    except Exception as e:
        return f"Could not read {app_name}'s window: {e}"

    if button is not None:
        try:
            button.click_input()
            return f"Answered an incoming {app_name} call (matched '{label}')."
        except Exception as e:
            return f"Found an accept button ('{label}') but could not click it: {e}"

    if any_text_matched:
        return (
            f"Something in {app_name}'s window looked like an incoming call, but no "
            f"accept button could be found in the same area - this needs calibration "
            f"(see the module docstring)."
        )
    return ""


def run_cycle(app_name: str = "WhatsApp") -> str:
    """Full cycle for the background poller (main.py). Returns '' when
    nothing happened (feature disabled, or nothing ringing) - callers
    should only log a non-empty result."""
    if not get_whatsapp_call_answer_enabled():
        return ""
    return check_and_answer(app_name)
