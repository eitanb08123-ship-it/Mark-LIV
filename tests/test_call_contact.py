"""
actions/call_contact.py tests. The actual "click the call button" step is
explicitly best-effort (no documented shortcut/API exists), so these tests
only pin what IS verifiable: which app gets opened for which platform,
that a missing receiver/pyautogui is handled without crashing, and that the
returned message never overclaims a confirmed connection.
"""
from types import SimpleNamespace

import pytest

from actions import call_contact


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(call_contact.time, "sleep", lambda s: None)


def test_missing_receiver_is_rejected_without_touching_the_desktop(monkeypatch):
    called = {"yes": False}
    monkeypatch.setattr(call_contact, "_open_app", lambda name: called.__setitem__("yes", True))

    result = call_contact.call_contact({"receiver": ""})

    assert "specify who to call" in result.lower()
    assert called["yes"] is False


def test_missing_pyautogui_is_reported_without_crashing(monkeypatch):
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", False)
    monkeypatch.setattr(call_contact, "pyautogui", None)

    result = call_contact.call_contact({"receiver": "Me"})

    assert "pyautogui" in result.lower()


def test_defaults_to_whatsapp_when_platform_omitted(monkeypatch):
    opened = {}
    monkeypatch.setattr(call_contact, "_open_app", lambda name: opened.setdefault("app", name) or True)
    monkeypatch.setattr(call_contact, "_search_in_app", lambda q: None)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    call_contact.call_contact({"receiver": "Me"})

    assert opened["app"] == "WhatsApp"


def test_telegram_platform_opens_telegram(monkeypatch):
    opened = {}
    monkeypatch.setattr(call_contact, "_open_app", lambda name: opened.setdefault("app", name) or True)
    monkeypatch.setattr(call_contact, "_search_in_app", lambda q: None)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    call_contact.call_contact({"receiver": "Me", "platform": "telegram"})

    assert opened["app"] == "Telegram"


def test_app_open_failure_is_reported(monkeypatch):
    monkeypatch.setattr(call_contact, "_open_app", lambda name: False)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    result = call_contact.call_contact({"receiver": "Me"})

    assert "could not open" in result.lower()


def test_success_message_never_claims_a_confirmed_connection(monkeypatch):
    """The whole point of this file's honesty note: unlike open_app's
    verified launches, there is no way to confirm a call actually connected -
    the message must not claim otherwise."""
    monkeypatch.setattr(call_contact, "_open_app", lambda name: True)
    monkeypatch.setattr(call_contact, "_search_in_app", lambda q: None)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    result = call_contact.call_contact({"receiver": "Me"})

    assert "attempted" in result.lower()
    assert "cannot confirm" in result.lower()


def test_unexpected_exception_is_reported_not_raised(monkeypatch):
    def _boom(name):
        raise RuntimeError("boom")
    monkeypatch.setattr(call_contact, "_open_app", _boom)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    result = call_contact.call_contact({"receiver": "Me"})

    assert "could not place the call" in result.lower()
