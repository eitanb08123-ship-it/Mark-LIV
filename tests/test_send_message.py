"""
actions/send_message.py tests. Regression coverage for the real bug found
live: this module used to have its own second "Win+search+Enter, then just
return True" app-launcher, completely separate from actions/open_app.py's
psutil-verified one - so it kept claiming "Message sent" even when the app
never actually opened. _open_app() now delegates to open_app.launch_app();
these tests pin that delegation and the rest of send_message()'s dispatch
logic (platform resolution, missing recipient/message/pyautogui).
"""
from types import SimpleNamespace

import pytest

from actions import send_message


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(send_message.time, "sleep", lambda s: None)


def test_paste_text_copies_and_pastes_when_clipboard_confirms(monkeypatch):
    copied = {}
    monkeypatch.setattr(send_message, "_PYPERCLIP", True)
    monkeypatch.setattr(send_message, "pyperclip", SimpleNamespace(
        copy=lambda t: copied.setdefault("text", t),
        paste=lambda: copied.get("text", ""),
    ), raising=False)
    pressed = []
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(hotkey=lambda *a: pressed.append(a)), raising=False)
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)

    send_message._paste_text("שלום")

    assert copied["text"] == "שלום"
    assert pressed == [("ctrl", "v")]


def test_paste_text_still_pastes_after_a_clipboard_timeout(monkeypatch, capsys):
    monkeypatch.setattr(send_message, "_PYPERCLIP", True)
    monkeypatch.setattr(send_message, "pyperclip", SimpleNamespace(copy=lambda t: None, paste=lambda: "stale"), raising=False)
    pressed = []
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(hotkey=lambda *a: pressed.append(a)), raising=False)
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
    monkeypatch.setattr(send_message, "_wait_for_clipboard", lambda text: False)

    send_message._paste_text("hi")

    assert pressed == [("ctrl", "v")]
    assert "clipboard" in capsys.readouterr().out.lower()


class _FakeControl:
    def __init__(self, text, control_type="Edit"):
        self._text = text
        self.control_type = control_type
        self.clicked = False

    def window_text(self):
        return self._text

    def click_input(self):
        self.clicked = True


class _FakeWinAutoWindow:
    """Mirrors the real pywinauto contract _click_search_control() relies
    on: descendants(control_type=X) filters at the "UIA" level, not in
    Python - the whole reason for filtering by type in the first place
    (see _SEARCH_CONTROL_TYPES's docstring: fetching every descendant
    unfiltered was what made this visibly slow live)."""
    def __init__(self, controls):
        self._controls = controls

    def descendants(self, control_type=None):
        if control_type is None:
            return self._controls
        return [c for c in self._controls if c.control_type == control_type]


def test_click_search_control_returns_false_without_pywinauto(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", False)
    assert send_message._click_search_control("WhatsApp") is False


def test_click_search_control_clicks_the_first_matching_control(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    other = _FakeControl("New chat")
    search = _FakeControl("Search or start a new chat")
    fake_win = _FakeWinAutoWindow([other, search])
    fake_app = SimpleNamespace(top_window=lambda: fake_win)
    monkeypatch.setattr(send_message, "Application",
                        lambda backend: SimpleNamespace(connect=lambda **kw: fake_app))

    assert send_message._click_search_control("WhatsApp") is True
    assert search.clicked is True
    assert other.clicked is False


def test_click_search_control_matches_hebrew_label(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    search = _FakeControl("חיפוש")
    fake_win = _FakeWinAutoWindow([search])
    fake_app = SimpleNamespace(top_window=lambda: fake_win)
    monkeypatch.setattr(send_message, "Application",
                        lambda backend: SimpleNamespace(connect=lambda **kw: fake_app))

    assert send_message._click_search_control("WhatsApp") is True
    assert search.clicked is True


def test_click_search_control_matches_a_button_typed_search_control(monkeypatch):
    """The search control isn't always an editable field - some apps
    expose it as a Button. Both control_type()s in _SEARCH_CONTROL_TYPES
    must actually be checked, not just the first one."""
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    search_button = _FakeControl("Search", control_type="Button")
    fake_win = _FakeWinAutoWindow([_FakeControl("New chat", control_type="Edit"), search_button])
    fake_app = SimpleNamespace(top_window=lambda: fake_win)
    monkeypatch.setattr(send_message, "Application",
                        lambda backend: SimpleNamespace(connect=lambda **kw: fake_app))

    assert send_message._click_search_control("WhatsApp") is True
    assert search_button.clicked is True


def test_click_search_control_only_scans_edit_and_button_types(monkeypatch):
    """Regression: this used to call descendants() with no control_type
    filter at all, walking every node in the window (hundreds to thousands
    once real chat history is loaded) - a real, observed performance
    problem live. A ListItem (chat row) matching 'search' in its text must
    never be found or clicked."""
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    decoy = _FakeControl("Search results from Dana", control_type="ListItem")
    fake_win = _FakeWinAutoWindow([decoy])
    fake_app = SimpleNamespace(top_window=lambda: fake_win)
    monkeypatch.setattr(send_message, "Application",
                        lambda backend: SimpleNamespace(connect=lambda **kw: fake_app))

    assert send_message._click_search_control("WhatsApp") is False
    assert decoy.clicked is False


def test_click_search_control_returns_false_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    fake_win = _FakeWinAutoWindow([_FakeControl("New chat"), _FakeControl("Settings")])
    fake_app = SimpleNamespace(top_window=lambda: fake_win)
    monkeypatch.setattr(send_message, "Application",
                        lambda backend: SimpleNamespace(connect=lambda **kw: fake_app))

    assert send_message._click_search_control("WhatsApp") is False


def test_click_search_control_survives_a_connection_failure(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)

    def _boom(backend):
        raise RuntimeError("window not found")

    monkeypatch.setattr(send_message, "Application", _boom)

    assert send_message._click_search_control("WhatsApp") is False


def test_search_in_app_prefers_clicking_over_ctrl_f_when_app_name_given(monkeypatch):
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
    monkeypatch.setattr(send_message, "_click_search_control", lambda app_name: True)
    hotkeys = []
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(hotkey=lambda *a: hotkeys.append(a)),
                        raising=False)
    monkeypatch.setattr(send_message, "_clear_and_paste", lambda text: None)

    send_message._search_in_app("Mom", "WhatsApp")

    assert hotkeys == []   # never fell back to Ctrl+F since the click succeeded


def test_search_in_app_falls_back_to_ctrl_f_when_click_fails(monkeypatch):
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
    monkeypatch.setattr(send_message, "_click_search_control", lambda app_name: False)
    hotkeys = []
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(hotkey=lambda *a: hotkeys.append(a)),
                        raising=False)
    monkeypatch.setattr(send_message, "_clear_and_paste", lambda text: None)

    send_message._search_in_app("Mom", "WhatsApp")

    assert hotkeys == [("ctrl", "f")]


def test_search_in_app_waits_after_a_successful_click_before_clearing(monkeypatch):
    """Regression: 'clicked search and stopped' - the post-hotkey delay
    used to only exist on the Ctrl+F fallback path, so a successful click
    went straight into Ctrl+A/Delete/paste with zero delay, possibly
    before the click's focus change had taken effect."""
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
    monkeypatch.setattr(send_message, "_click_search_control", lambda app_name: True)
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(hotkey=lambda *a: None), raising=False)
    monkeypatch.setattr(send_message, "_clear_and_paste", lambda text: None)
    slept = []
    monkeypatch.setattr(send_message.time, "sleep", lambda s: slept.append(s))

    send_message._search_in_app("Mom", "WhatsApp")

    assert slept and slept[0] > 0


def test_search_in_app_uses_ctrl_f_when_no_app_name_given(monkeypatch):
    """Browser-based callers (_send_messenger) don't pass app_name - must
    never try the pywinauto click path in that case."""
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
    called = {"click": False}
    monkeypatch.setattr(send_message, "_click_search_control",
                        lambda app_name: called.__setitem__("click", True) or True)
    hotkeys = []
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(hotkey=lambda *a: hotkeys.append(a)),
                        raising=False)
    monkeypatch.setattr(send_message, "_clear_and_paste", lambda text: None)

    send_message._search_in_app("Mom")

    assert called["click"] is False
    assert hotkeys == [("ctrl", "f")]


# ── _ensure_foreground (item 3: process exists != window is focused) ───────

class _FakeFocusWindow:
    def __init__(self, handle=123):
        self.handle = handle
        self.focused = False

    def set_focus(self):
        self.focused = True


def test_ensure_foreground_returns_none_without_pywinauto(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", False)
    monkeypatch.setattr(send_message, "_WIN32GUI", True)

    assert send_message._ensure_foreground("WhatsApp") is None


def test_ensure_foreground_returns_none_without_win32gui(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    monkeypatch.setattr(send_message, "_WIN32GUI", False)

    assert send_message._ensure_foreground("WhatsApp") is None


def test_ensure_foreground_true_when_window_becomes_foreground(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    monkeypatch.setattr(send_message, "_WIN32GUI", True)
    win = _FakeFocusWindow(handle=123)
    fake_app = SimpleNamespace(connect=lambda **kw: SimpleNamespace(top_window=lambda: win))
    monkeypatch.setattr(send_message, "Application", lambda backend: fake_app)
    monkeypatch.setattr(send_message, "win32gui", SimpleNamespace(GetForegroundWindow=lambda: 123), raising=False)

    assert send_message._ensure_foreground("WhatsApp") is True
    assert win.focused is True


def test_ensure_foreground_false_when_a_different_window_has_focus(monkeypatch):
    """The exact safety gap: WhatsApp's process/window exists, set_focus()
    was called, but a DIFFERENT window (e.g. the browser the user is
    actively using) is still the real foreground window."""
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    monkeypatch.setattr(send_message, "_WIN32GUI", True)
    win = _FakeFocusWindow(handle=123)
    fake_app = SimpleNamespace(connect=lambda **kw: SimpleNamespace(top_window=lambda: win))
    monkeypatch.setattr(send_message, "Application", lambda backend: fake_app)
    monkeypatch.setattr(send_message, "win32gui", SimpleNamespace(GetForegroundWindow=lambda: 999), raising=False)

    assert send_message._ensure_foreground("WhatsApp") is False


def test_ensure_foreground_returns_none_on_connection_failure(monkeypatch):
    monkeypatch.setattr(send_message, "_PYWINAUTO", True)
    monkeypatch.setattr(send_message, "_WIN32GUI", True)

    def _boom(backend):
        raise RuntimeError("window not found")
    monkeypatch.setattr(send_message, "Application", _boom)
    monkeypatch.setattr(send_message, "win32gui", SimpleNamespace(GetForegroundWindow=lambda: 1), raising=False)

    assert send_message._ensure_foreground("WhatsApp") is None


def test_desktop_send_aborts_when_focus_is_confirmed_absent(monkeypatch):
    monkeypatch.setattr(send_message, "_open_app", lambda name: True)
    monkeypatch.setattr(send_message, "_ensure_foreground", lambda name: False)
    searched = {"called": False}
    monkeypatch.setattr(send_message, "_search_in_app", lambda q, app_name="": searched.__setitem__("called", True))

    result = send_message._desktop_send("WhatsApp", "Dana", "hi")

    assert searched["called"] is False
    assert "focus" in result.lower()


def test_desktop_send_proceeds_when_focus_cannot_be_checked(monkeypatch):
    monkeypatch.setattr(send_message, "_open_app", lambda name: True)
    monkeypatch.setattr(send_message, "_ensure_foreground", lambda name: None)
    monkeypatch.setattr(send_message, "_search_in_app", lambda q, app_name="": None)
    monkeypatch.setattr(send_message, "_paste_text", lambda text: None)
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None), raising=False)

    result = send_message._desktop_send("WhatsApp", "Dana", "hi")

    assert "sent" in result.lower()


def test_desktop_send_proceeds_when_focus_is_confirmed(monkeypatch):
    monkeypatch.setattr(send_message, "_open_app", lambda name: True)
    monkeypatch.setattr(send_message, "_ensure_foreground", lambda name: True)
    monkeypatch.setattr(send_message, "_search_in_app", lambda q, app_name="": None)
    monkeypatch.setattr(send_message, "_paste_text", lambda text: None)
    monkeypatch.setattr(send_message, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None), raising=False)

    result = send_message._desktop_send("WhatsApp", "Dana", "hi")

    assert "sent" in result.lower()


def test_open_app_delegates_to_open_app_module(monkeypatch):
    calls = []
    monkeypatch.setattr(send_message, "_launch_app", lambda name: calls.append(name) or True)

    result = send_message._open_app("WhatsApp")

    assert result is True
    assert calls == ["WhatsApp"]


def test_open_app_reports_failure_from_launch_app(monkeypatch):
    monkeypatch.setattr(send_message, "_launch_app", lambda name: False)

    assert send_message._open_app("WhatsApp") is False


def test_send_message_requires_a_receiver():
    result = send_message.send_message({"message_text": "hi", "platform": "whatsapp"})
    assert "recipient" in result.lower()


def test_send_message_requires_message_text():
    result = send_message.send_message({"receiver": "Dana", "platform": "whatsapp"})
    assert "message content" in result.lower()


def test_send_message_reports_missing_pyautogui(monkeypatch):
    monkeypatch.setattr(send_message, "_PYAUTOGUI", False)

    result = send_message.send_message({"receiver": "Dana", "message_text": "hi", "platform": "whatsapp"})

    assert "pyautogui" in result.lower()


def test_send_message_dispatches_to_the_resolved_platform_handler(monkeypatch):
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
    captured = {}

    def _fake_handler(receiver, message):
        captured["receiver"] = receiver
        captured["message"] = message
        return "Message sent to Dana via WhatsApp."

    monkeypatch.setattr(send_message, "_resolve_platform", lambda platform: _fake_handler)

    result = send_message.send_message({"receiver": "Dana", "message_text": "hi", "platform": "whatsapp"})

    assert captured == {"receiver": "Dana", "message": "hi"}
    assert "sent" in result.lower()


def test_send_message_reports_handler_exceptions_without_raising(monkeypatch):
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)

    def _boom(receiver, message):
        raise RuntimeError("window not found")

    monkeypatch.setattr(send_message, "_resolve_platform", lambda platform: _boom)

    result = send_message.send_message({"receiver": "Dana", "message_text": "hi", "platform": "whatsapp"})

    assert "window not found" in result


def test_send_message_logs_to_player_when_given(monkeypatch):
    monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
    monkeypatch.setattr(send_message, "_resolve_platform", lambda platform: lambda r, m: "Message sent to Dana via WhatsApp.")
    logged = []
    player = SimpleNamespace(write_log=lambda msg: logged.append(msg))

    send_message.send_message({"receiver": "Dana", "message_text": "hi", "platform": "whatsapp"}, player=player)

    assert any("Dana" in msg for msg in logged)


@pytest.mark.parametrize("platform_str,expected_keyword", [
    ("whatsapp", "whatsapp"),
    ("WP", "whatsapp"),
    ("telegram", "telegram"),
    ("instagram", "instagram"),
    ("insta", "instagram"),
    ("signal", "signal"),
    ("discord", "discord"),
    ("messenger", "messenger"),
    ("facebook", "messenger"),
])
def test_resolve_platform_maps_known_aliases(platform_str, expected_keyword):
    handler = send_message._resolve_platform(platform_str)
    assert handler.__name__ == f"_send_{expected_keyword}"


def test_resolve_platform_falls_back_to_desktop_send_for_unknown_platforms(monkeypatch):
    called = {}
    monkeypatch.setattr(send_message, "_desktop_send",
                        lambda app, receiver, message: called.setdefault("app", app) or "ok")

    handler = send_message._resolve_platform("Snapchat")
    handler("Dana", "hi")

    assert called["app"] == "Snapchat"


def test_resolve_platform_does_not_let_a_short_alias_match_inside_another_word():
    """Regression: 'ig' (Instagram's alias) is also a substring of
    'signal', so a plain request to message via Signal was silently being
    routed to the Instagram handler instead."""
    handler = send_message._resolve_platform("signal")
    assert handler.__name__ == "_send_signal"
