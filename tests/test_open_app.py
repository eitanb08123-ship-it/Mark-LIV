"""
actions/open_app.py tests. Every launcher used to return True the instant its
own subprocess/automation call didn't raise an exception - even when the
shell command silently failed, or simulated Start-Menu/Spotlight keystrokes
landed in the wrong window and never actually opened anything. `psutil` was
imported for exactly this verification but sat unused. These tests pin the
fixed behavior: a launch is only reported successful once a matching process
is actually observed running (when psutil is available at all).
"""
import sys
from types import SimpleNamespace

import pytest

from actions import open_app


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(open_app.time, "sleep", lambda s: None)


def _fake_proc(name):
    return SimpleNamespace(info={"name": name})


class _FakePsutil:
    def __init__(self, names):
        self._names = names

    def process_iter(self, fields):
        return [_fake_proc(n) for n in self._names]


def test_is_process_running_true_when_matching_process_exists(monkeypatch):
    monkeypatch.setattr(open_app, "_PSUTIL", True)
    monkeypatch.setattr(open_app, "psutil", _FakePsutil(["chrome.exe"]), raising=False)
    assert open_app._is_process_running("chrome") is True


def test_is_process_running_false_when_no_matching_process(monkeypatch):
    monkeypatch.setattr(open_app, "_PSUTIL", True)
    monkeypatch.setattr(open_app, "psutil", _FakePsutil(["explorer.exe"]), raising=False)
    assert open_app._is_process_running("chrome", timeout=0.05, poll=0.01) is False


def test_is_process_running_assumes_success_without_psutil(monkeypatch):
    monkeypatch.setattr(open_app, "_PSUTIL", False)
    assert open_app._is_process_running("anything") is True


def test_windows_falls_through_to_start_menu_when_popen_did_not_really_open_it(monkeypatch):
    """The historical bug: shutil.which() finds a same-named binary and
    Popen() doesn't raise, but the app never actually appears - the old code
    declared victory right there. Now it must keep trying."""
    monkeypatch.setattr(open_app.shutil, "which", lambda name: "/usr/bin/chrome")
    monkeypatch.setattr(open_app.subprocess, "Popen", lambda *a, **kw: None)
    monkeypatch.setattr(open_app, "_is_process_running", lambda name, **kw: False)

    fake_pyautogui = SimpleNamespace(
        PAUSE=0, press=lambda *a, **kw: None, write=lambda *a, **kw: None,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    assert open_app._launch_windows("chrome") is False


def test_windows_start_menu_fallback_succeeds_once_process_is_verified(monkeypatch):
    monkeypatch.setattr(open_app.shutil, "which", lambda name: None)
    # Deterministic regardless of whether pyperclip happens to be installed
    # in the environment this test runs in (a real pyperclip.copy() would
    # fail on a headless Linux box with no clipboard tool) - this test is
    # about the Start-Menu-fallback/verification path generically, not
    # about clipboard behavior specifically (see test_start_menu_fallback_*
    # below for that).
    monkeypatch.setattr(open_app, "_PYPERCLIP", False)

    fake_pyautogui = SimpleNamespace(
        PAUSE=0, press=lambda *a, **kw: None, write=lambda *a, **kw: None,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(open_app, "_is_process_running", lambda name, **kw: True)

    assert open_app._launch_windows("notepad.exe") is True


def test_windows_reports_failure_when_nothing_works(monkeypatch):
    monkeypatch.setattr(open_app.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "pyautogui", None)   # import pyautogui -> ImportError

    assert open_app._launch_windows("some_nonexistent_app") is False


def test_start_menu_fallback_pastes_the_app_name_instead_of_typing_it(monkeypatch):
    """Regression: pyautogui.write() sends raw key events, which map to
    whatever the ACTIVE KEYBOARD LAYOUT says a physical key produces.
    Typing "WhatsApp" while a Hebrew layout is active does not type
    W-h-a-t-s-A-p-p - it types whatever Hebrew letters share those key
    positions (observed live: "ישאדשפפ"). _type_app_name() must paste via
    the clipboard (layout-independent) whenever pyperclip is available,
    never call pyautogui.write() with the app name in that case."""
    monkeypatch.setattr(open_app, "_PYPERCLIP", True)
    copied = {}
    monkeypatch.setattr(open_app, "pyperclip", SimpleNamespace(copy=lambda t: copied.setdefault("text", t)),
                        raising=False)
    calls = {"write": False, "hotkey": []}
    fake_pyautogui = SimpleNamespace(
        PAUSE=0, press=lambda *a, **kw: None,
        write=lambda *a, **kw: calls.__setitem__("write", True),
        hotkey=lambda *a, **kw: calls["hotkey"].append(a),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(open_app.shutil, "which", lambda name: None)
    monkeypatch.setattr(open_app, "_is_process_running", lambda name, **kw: True)

    assert open_app._launch_windows("WhatsApp") is True
    assert copied["text"] == "WhatsApp"
    assert calls["hotkey"] == [("ctrl", "v")]
    assert calls["write"] is False


def test_type_app_name_falls_back_to_write_without_pyperclip(monkeypatch):
    monkeypatch.setattr(open_app, "_PYPERCLIP", False)
    calls = {}
    fake_pyautogui = SimpleNamespace(write=lambda text, **kw: calls.setdefault("text", text))
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    open_app._type_app_name("WhatsApp")

    assert calls["text"] == "WhatsApp"


def test_type_app_name_waits_for_the_clipboard_before_pasting(monkeypatch):
    """Regression: 'pressed Win and just stopped' - _type_app_name() used
    to copy() and paste with only a fixed sleep() in between, the same
    clipboard-vs-Qt race already fixed once in
    actions/send_message.py::_paste_text(). A stale/empty paste into the
    Start Menu search finds nothing to open."""
    monkeypatch.setattr(open_app, "_PYPERCLIP", True)
    waited = {}
    monkeypatch.setattr(open_app, "wait_for_clipboard", lambda text: waited.setdefault("text", text) or True)
    monkeypatch.setattr(open_app, "pyperclip", SimpleNamespace(copy=lambda t: None), raising=False)
    pressed = []
    fake_pyautogui = SimpleNamespace(hotkey=lambda *a: pressed.append(a))
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    open_app._type_app_name("WhatsApp")

    assert waited["text"] == "WhatsApp"
    assert pressed == [("ctrl", "v")]


def test_wait_for_clipboard_returns_true_as_soon_as_it_matches(monkeypatch):
    monkeypatch.setattr(open_app, "_PYPERCLIP", True)
    monkeypatch.setattr(open_app, "pyperclip", SimpleNamespace(paste=lambda: "hello"), raising=False)

    assert open_app.wait_for_clipboard("hello") is True


def test_wait_for_clipboard_polls_until_it_matches(monkeypatch):
    monkeypatch.setattr(open_app, "_PYPERCLIP", True)
    calls = {"n": 0}

    def _paste():
        calls["n"] += 1
        return "hello" if calls["n"] >= 3 else "stale"

    monkeypatch.setattr(open_app, "pyperclip", SimpleNamespace(paste=_paste), raising=False)

    assert open_app.wait_for_clipboard("hello") is True
    assert calls["n"] == 3


def test_wait_for_clipboard_gives_up_after_timeout(monkeypatch):
    monkeypatch.setattr(open_app, "_PYPERCLIP", True)
    monkeypatch.setattr(open_app, "pyperclip", SimpleNamespace(paste=lambda: "stale"), raising=False)
    # time.sleep is a no-op (autouse fixture), so time.time() driving the
    # deadline is what actually needs to elapse - use a tiny timeout so the
    # test doesn't really wait a full second.
    result = open_app.wait_for_clipboard("hello", timeout=0.01, poll=0.001)

    assert result is False


def test_wait_for_clipboard_survives_a_paste_error(monkeypatch):
    monkeypatch.setattr(open_app, "_PYPERCLIP", True)

    def _boom():
        raise RuntimeError("clipboard busy")

    monkeypatch.setattr(open_app, "pyperclip", SimpleNamespace(paste=_boom), raising=False)

    result = open_app.wait_for_clipboard("hello", timeout=0.01, poll=0.001)

    assert result is False


def test_wait_for_clipboard_returns_false_immediately_without_pyperclip(monkeypatch):
    monkeypatch.setattr(open_app, "_PYPERCLIP", False)

    assert open_app.wait_for_clipboard("hello", timeout=1.0) is False


def test_linux_xdg_open_failure_return_code_is_not_treated_as_success(monkeypatch):
    """xdg-open used to be trusted just for not raising - a nonzero exit
    (e.g. 'no application knows how to open this') was silently ignored."""
    monkeypatch.setattr(open_app.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        open_app.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=1),
    )

    assert open_app._launch_linux("totally_unknown_app") is False


def test_linux_xdg_open_success_return_code_is_treated_as_success(monkeypatch):
    monkeypatch.setattr(open_app.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        open_app.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=0),
    )

    assert open_app._launch_linux("firefox") is True


def test_open_app_reports_honest_failure_message(monkeypatch):
    monkeypatch.setattr(open_app, "_OS_LAUNCHERS", {open_app._SYSTEM: lambda name: False})

    result = open_app.open_app({"app_name": "Nonexistent App"})

    assert "Could not confirm" in result


def test_open_app_reports_success_when_launcher_confirms(monkeypatch):
    monkeypatch.setattr(open_app, "_OS_LAUNCHERS", {open_app._SYSTEM: lambda name: True})

    result = open_app.open_app({"app_name": "Chrome"})

    assert result == "Opened Chrome."


def test_normalize_maps_cs2_to_a_steam_launch_uri_not_the_bare_name():
    """Regression: 'CS2' used to normalize to the literal string 'CS2' and get
    typed into the OS search box, which only works if a shortcut happens to
    exist with that exact name - it didn't, so nothing opened."""
    assert open_app._normalize("CS2") == "steam://rungameid/730"
    assert open_app._normalize("counter-strike 2") == "steam://rungameid/730"


def test_macos_handles_uri_scheme_apps(monkeypatch):
    monkeypatch.setattr(
        open_app.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=0),
    )
    assert open_app._launch_macos("steam://rungameid/730") is True


def test_macos_uri_scheme_failure_falls_through_to_open_dash_a(monkeypatch):
    calls = []

    def _fake_run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(open_app.subprocess, "run", _fake_run)
    monkeypatch.setattr(open_app.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", None)

    assert open_app._launch_macos("steam://rungameid/730") is False
    assert ["open", "steam://rungameid/730"] in calls
