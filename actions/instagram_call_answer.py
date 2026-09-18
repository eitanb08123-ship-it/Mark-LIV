"""
actions/instagram_call_answer.py — EXPERIMENTAL: auto-accepts an incoming
Instagram voice/video call in Direct Messages.

Reuses actions/browser_control.py's own persistent Playwright session
(_registry) instead of a separate automation stack - Playwright gives real
DOM access (locators by ARIA role / visible text / aria-label, via
smart_click()'s existing role -> text -> placeholder -> aria-label fallback
chain), which is far more reliable for browser-rendered content than the
OS-level UI-Automation guessing actions/auto_reply.py has to use for
WhatsApp/Telegram Desktop (browsers don't reliably expose a full
accessibility tree the way Electron desktop apps do).

Soft-imports browser_control: if playwright isn't installed, this module
still imports cleanly (main.py depends on it unconditionally) and every
public function just reports that plainly instead of crashing - the same
pattern core/claude_client.py uses for the optional anthropic package.

VISIBLE SIDE EFFECT worth knowing: enabling this keeps a real, visible
browser window open on Instagram's inbox at all times - it is not hidden
or headless (Instagram requires a real logged-in session), but unlike
actions/call_contact.py or actions/auto_reply.py, it does NOT touch the
user's actual mouse/keyboard - Playwright dispatches clicks straight to the
page/element over its own protocol, so there's nothing of the user's
input to steal, and no idle-time guard is needed here the way the
pyautogui-based features need one.

HONESTY NOTE: Instagram's incoming-call UI text/labels were never
inspected live while writing this (no access to the user's account/session
from here). _CALL_INDICATOR_TEXTS and _ACCEPT_LABELS include both English
and Hebrew guesses (every screenshot shared while building this feature
showed a Hebrew UI) - if the wording still doesn't match, this will detect
nothing. Calibrate with actions.browser_control's own get_text/screenshot
actions pointed at the inbox while a real call is ringing.

Off by default. Turn on with
memory.config_manager.save_instagram_auto_answer_enabled(True).
"""
from __future__ import annotations

from memory.config_manager import get_instagram_auto_answer_enabled

try:
    from actions.browser_control import _registry
    _BROWSER_CONTROL_AVAILABLE = True
except ImportError:
    _registry = None
    _BROWSER_CONTROL_AVAILABLE = False

_INBOX_URL = "https://www.instagram.com/direct/inbox/"

_CALL_INDICATOR_TEXTS = (
    "is calling", "incoming call", "calling you", "video call", "voice call",
    "מתקשר", "מתקשרת", "שיחה נכנסת", "שיחת וידאו", "שיחה קולית",
)
_ACCEPT_LABELS = ("Accept", "Answer", "Join call", "Join", "קבל", "ענה", "הצטרף")


def _get_session(browser_name: str = "chrome"):
    return _registry.get(browser_name)


def ensure_on_inbox(browser_name: str = "chrome") -> str:
    """Makes sure the persistent session is actually sitting on Instagram's
    inbox - call before polling starts. Never raises."""
    if not _BROWSER_CONTROL_AVAILABLE:
        return "browser_control (playwright) is not available."
    session = _get_session(browser_name)
    try:
        url = session.run(session.get_url(), timeout=15)
    except Exception as e:
        return f"Could not check the browser session: {e}"
    if "instagram.com" in (url or ""):
        return f"Already on: {url}"
    try:
        return session.run(session.go_to(_INBOX_URL), timeout=30)
    except Exception as e:
        return f"Could not open Instagram: {e}"


def check_and_answer(browser_name: str = "chrome") -> str:
    """One poll: reads the current page text, and if it looks like a call
    is ringing, tries each known accept-button label in turn. Returns ''
    when nothing is ringing (the common case) - never raises."""
    if not _BROWSER_CONTROL_AVAILABLE:
        return "browser_control (playwright) is not available."

    session = _get_session(browser_name)
    try:
        text = session.run(session.get_text(), timeout=15)
    except Exception as e:
        return f"Could not read the Instagram page: {e}"

    lowered = (text or "").lower()
    if not any(marker in lowered for marker in _CALL_INDICATOR_TEXTS):
        return ""

    for label in _ACCEPT_LABELS:
        try:
            result = session.run(session.smart_click(label), timeout=10)
        except Exception:
            continue
        if result.startswith("Clicked"):
            return f"Answered an incoming Instagram call (matched '{label}')."

    return (
        "Something on the page looked like an incoming call, but none of the "
        "known accept-button labels could be clicked - this needs calibration "
        "(see the module docstring)."
    )


def run_cycle(browser_name: str | None = None) -> str:
    """Full cycle for the background poller (main.py): ensures the session
    is on the inbox, then checks for and answers a ringing call. Returns ''
    when nothing happened (feature disabled, or nothing ringing) - callers
    should only log a non-empty result."""
    if not get_instagram_auto_answer_enabled():
        return ""
    browser_name = browser_name or "chrome"
    ensure_on_inbox(browser_name)
    return check_and_answer(browser_name)
