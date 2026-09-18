"""
actions/auto_reply.py tests. The "detect an unread message" step is
explicitly uncalibrated (see the module docstring - it was never checked
against a real running WhatsApp/Telegram instance), so these tests pin
what IS verifiable in isolation: the off-by-default gate, graceful
handling of missing pyautogui/pywinauto, the reply-generation prompt
requiring the incoming message's own language, and that a chat row's
first line is used as the contact to reply to.
"""
from types import SimpleNamespace

import pytest

from actions import auto_reply


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(auto_reply.time, "sleep", lambda s: None)


def test_disabled_by_default_returns_empty_without_touching_anything(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: False)
    called = {"yes": False}
    monkeypatch.setattr(auto_reply, "_find_unread_chats", lambda app: called.__setitem__("yes", True) or [])

    result = auto_reply.auto_reply_cycle("WhatsApp")

    assert result == []
    assert called["yes"] is False


def test_missing_pyautogui_is_reported(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", False)
    monkeypatch.setattr(auto_reply, "pyautogui", None)

    result = auto_reply.auto_reply_cycle("WhatsApp")

    assert any("pyautogui" in r.lower() for r in result)


def test_missing_pywinauto_is_reported(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", True)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", False)

    result = auto_reply.auto_reply_cycle("WhatsApp")

    assert any("pywinauto" in r.lower() for r in result)


def test_no_unread_chats_means_no_replies(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", True)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    monkeypatch.setattr(auto_reply, "_find_unread_chats", lambda app: [])

    result = auto_reply.auto_reply_cycle("WhatsApp")

    assert result == []


def test_generate_reply_prompt_requires_matching_the_incoming_language(monkeypatch):
    """The user's explicit requirement: reply in the same language as the
    incoming message, never a different one."""
    captured = {}

    def _fake_text(prompt, **kw):
        captured["prompt"] = prompt
        return "OK reply"

    monkeypatch.setattr(auto_reply.gemini, "text", _fake_text)

    result = auto_reply._generate_reply("שלום, מה קורה?")

    assert result == "OK reply"
    assert "same language as the incoming message" in captured["prompt"]
    assert "שלום, מה קורה?" in captured["prompt"]


def test_reply_to_chat_uses_first_line_of_row_as_contact_and_sends(monkeypatch):
    opened = {}
    searched = {}
    pasted = {}

    monkeypatch.setattr(auto_reply, "_generate_reply", lambda text: "Sure, on it!")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: opened.setdefault("app", name) or True)
    monkeypatch.setattr(auto_reply, "_search_in_app", lambda q: searched.setdefault("query", q))
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: pasted.setdefault("text", text))
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert opened["app"] == "WhatsApp"
    assert searched["query"] == "Dana"
    assert pasted["text"] == "Sure, on it!"
    assert "Dana" in result
    assert "Sure, on it!" in result


def test_reply_to_chat_skips_sending_when_gemini_produces_nothing(monkeypatch):
    called = {"opened": False}
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda text: "")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert called["opened"] is False
    assert "no reply" in result.lower()


def test_auto_reply_cycle_replies_to_each_unread_chat(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", True)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    monkeypatch.setattr(auto_reply, "_find_unread_chats", lambda app: ["Dana\nunread", "Yossi\nunread"])
    monkeypatch.setattr(auto_reply, "_reply_to_chat", lambda app, row: f"replied to {row.splitlines()[0]}")

    result = auto_reply.auto_reply_cycle("WhatsApp")

    assert result == ["replied to Dana", "replied to Yossi"]


def test_auto_reply_cycle_one_bad_chat_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", True)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    monkeypatch.setattr(auto_reply, "_find_unread_chats", lambda app: ["Dana\nunread", "Yossi\nunread"])

    def _flaky(app, row):
        if row.startswith("Dana"):
            raise RuntimeError("boom")
        return "replied to Yossi"

    monkeypatch.setattr(auto_reply, "_reply_to_chat", _flaky)

    result = auto_reply.auto_reply_cycle("WhatsApp")

    assert len(result) == 2
    assert "boom" in result[0]
    assert result[1] == "replied to Yossi"


def test_find_unread_chats_matches_rows_mentioning_unread(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    fake_rows = [
        SimpleNamespace(window_text=lambda: "Dana\n3 unread messages"),
        SimpleNamespace(window_text=lambda: "Mom\nSee you tonight"),
        SimpleNamespace(window_text=lambda: "Work group\n1 unread message"),
    ]
    fake_win = SimpleNamespace(descendants=lambda control_type: fake_rows)
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name, timeout=5.0: fake_win)

    result = auto_reply._find_unread_chats("WhatsApp")

    assert result == ["Dana\n3 unread messages", "Work group\n1 unread message"]


def test_find_unread_chats_returns_empty_without_pywinauto(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", False)
    assert auto_reply._find_unread_chats("WhatsApp") == []


def test_find_unread_chats_never_raises_on_connection_failure(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)

    def _boom(app_name, timeout=5.0):
        raise RuntimeError("window not found")
    monkeypatch.setattr(auto_reply, "_connect", _boom)

    assert auto_reply._find_unread_chats("WhatsApp") == []


def test_inspect_chat_window_reports_missing_pywinauto(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", False)
    result = auto_reply.inspect_chat_window("WhatsApp")
    assert "pywinauto" in result.lower()


def test_inspect_chat_window_dumps_the_control_tree(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)

    def _print_tree(depth=3):
        print("Dialog - 'WhatsApp'\n    ListItem - 'Dana'")

    fake_win = SimpleNamespace(print_control_identifiers=_print_tree)
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name, timeout=5.0: fake_win)

    result = auto_reply.inspect_chat_window("WhatsApp")

    assert "Dana" in result


def test_inspect_chat_window_reports_connection_failure(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)

    def _boom(app_name, timeout=5.0):
        raise RuntimeError("not open")
    monkeypatch.setattr(auto_reply, "_connect", _boom)

    result = auto_reply.inspect_chat_window("WhatsApp")

    assert "could not connect" in result.lower()
