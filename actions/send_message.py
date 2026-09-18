import json
import subprocess
import sys
import time
from pathlib import Path

from actions.open_app import launch_app as _launch_app

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

try:
    from pywinauto import Application
    _PYWINAUTO = True
except ImportError:
    _PYWINAUTO = False
    Application = None

_WINDOW_TITLE_PATTERNS = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
}

# English and Hebrew: every screenshot shared while building this project
# showed a Hebrew Windows/app UI (see actions/auto_reply.py's own
# _UNREAD_MARKERS for the same reasoning).
_SEARCH_CONTROL_MARKERS = ("search", "חיפוש", "find")

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _wait_for_clipboard(text: str, timeout: float = 1.0, poll: float = 0.05) -> bool:
    """Polls pyperclip.paste() until it matches what was just copied, or
    `timeout` runs out. Exists because of a real, observed race: this
    process also runs a PyQt GUI, and Windows' clipboard logged
    'qt.qpa.mime: Retrying to obtain clipboard' contention right around a
    paste - a single fixed sleep() after copy() isn't a reliable enough
    guarantee that Ctrl+V will paste the NEW text and not something stale.
    Returns False (not True) on timeout so the caller can at least log it -
    still proceeds either way, since there's nothing better to fall back to
    once pyautogui.hotkey() actually fires (see the caller)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pyperclip.paste() == text:
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        if not _wait_for_clipboard(text):
            print("[SendMessage] ⚠️ Clipboard did not confirm the new text before pasting - proceeding anyway.")
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    """Delegates to actions/open_app.py's launch_app() - this used to be a
    second, independent "Win+search+Enter, then just assume it worked"
    implementation that never got the psutil-based verification open_app.py
    was fixed to do (a real bug: this module claimed "Message sent" even
    when the app never actually opened, e.g. because the wrong window had
    focus). One verified launcher now, not two copies that can drift."""
    return _launch_app(app_name)


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0) 
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False

# A search box in a chat app is an editable field or a button - never
# ListItem/Text/whatever else fills a chat window (hundreds to thousands
# of nodes once real chat history is loaded). Asking pywinauto to filter
# by control_type is a UIA-level property match (fast); fetching every
# descendant unfiltered and checking each one's text in Python is what
# made this visibly slow live - the same lesson _find_unread_chats()
# already applied by filtering to control_type="ListItem".
_SEARCH_CONTROL_TYPES = ("Edit", "Button")


def _click_search_control(app_name: str) -> bool:
    """Best-effort: connects to app_name's already-open window (pywinauto)
    and clicks whatever descendant looks like a search box/button (English
    or Hebrew label - _SEARCH_CONTROL_MARKERS), instead of assuming Ctrl+F
    reveals one. Observed live: WhatsApp Desktop's search is a real
    clickable element in the chat-list pane - Ctrl+F alone left the app
    doing nothing further after Ctrl+A/Delete, since there was never a
    search box for that text to land in. Returns False on any failure or
    when pywinauto isn't installed - the caller falls back to Ctrl+F."""
    if not _PYWINAUTO:
        return False
    pattern = _WINDOW_TITLE_PATTERNS.get(app_name.lower(), app_name)
    try:
        win = Application(backend="uia").connect(title_re=f".*{pattern}.*", timeout=5.0).top_window()
        for control_type in _SEARCH_CONTROL_TYPES:
            for ctrl in win.descendants(control_type=control_type):
                try:
                    name = (ctrl.window_text() or "").strip().lower()
                except Exception:
                    continue
                if name and any(marker in name for marker in _SEARCH_CONTROL_MARKERS):
                    ctrl.click_input()
                    return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not click a search control in {app_name}: {e}")
    return False


def _search_in_app(query: str, app_name: str = "") -> None:
    """Opens the app's search and types `query` into it. If `app_name` is
    given (a real native desktop window - see _desktop_send()), tries
    clicking an actual search control first; only falls back to the Ctrl+F
    shortcut when that's unavailable or finds nothing - Ctrl+F was proven
    unreliable live (see _click_search_control's docstring). `app_name`
    is deliberately omitted by browser-based callers (_send_messenger) -
    pywinauto's UI Automation doesn't reliably see into a browser's own
    DOM the way it does an Electron desktop app's controls."""
    _require_pyautogui()

    if not (app_name and _click_search_control(app_name)):
        os_name = _get_os()
        search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")
        pyautogui.hotkey(*search_hotkey)
        time.sleep(0.5)

    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver, app_name)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)
    return f"Message sent to {receiver} via {app_name}."

def _send_whatsapp(receiver: str, message: str) -> str:
    return _desktop_send("WhatsApp", receiver, message)

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in browser."

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

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Instagram."


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return "Could not open Messenger in browser."


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Messenger."

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    # Exact match first: a bare 2-letter alias like "ig" (Instagram) or "tg"
    # (Telegram) is also a substring of unrelated words - "ig" inside
    # "signal" was matching Instagram's handler for a plain "signal"
    # request. Substring matching stays, but only for aliases long enough
    # (>2 chars) that a false-positive match inside another word is
    # implausible, and only once no keyword matched the input exactly.
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if key in keywords:
            return handler
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords if len(k) > 2):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        handler = _resolve_platform(platform)
        result  = handler(receiver, message_text)
    except Exception as e:
        result = f"Could not send message: {e}"

    print(f"[SendMessage] {'✅' if 'sent' in result.lower() else '❌'} {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "send_message",
    "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Recipient contact name"
            },
            "message_text": {
                "type": "STRING",
                "description": "The message to send"
            },
            "platform": {
                "type": "STRING",
                "description": "Platform: WhatsApp, Telegram, etc."
            }
        },
        "required": [
            "receiver",
            "message_text",
            "platform"
        ]
    },
    "handler": send_message,
}
