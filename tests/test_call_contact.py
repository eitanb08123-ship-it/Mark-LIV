"""
actions/call_contact.py tests. The actual "click the call button" step is
explicitly best-effort (no documented shortcut/API exists), so these tests
focus on what IS verifiable: that it can ONLY ever call the configured
owner contact (never an arbitrary name, even one supplied in parameters),
that a missing configuration/pyautogui is handled without crashing, and
that the returned message never overclaims a confirmed connection.
"""
from types import SimpleNamespace

import pytest

from actions import call_contact


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(call_contact.time, "sleep", lambda s: None)


@pytest.fixture
def working_desktop(monkeypatch):
    """A pyautogui/open_app/search_in_app stack that always 'succeeds', and
    records which contact name was actually searched for."""
    opened = {}
    searched = {}
    monkeypatch.setattr(call_contact, "_open_app", lambda name: opened.setdefault("app", name) or True)
    monkeypatch.setattr(call_contact, "_search_in_app", lambda q: searched.setdefault("query", q))
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)
    return opened, searched


def test_no_owner_configured_refuses_without_touching_the_desktop(monkeypatch):
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "")
    called = {"yes": False}
    monkeypatch.setattr(call_contact, "_open_app", lambda name: called.__setitem__("yes", True))

    result = call_contact.call_contact({})

    assert "no owner contact is configured" in result.lower()
    assert called["yes"] is False


def test_always_calls_the_configured_owner_contact(monkeypatch, working_desktop):
    opened, searched = working_desktop
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")

    call_contact.call_contact({})

    assert searched["query"] == "Eitan"


def test_a_supplied_receiver_parameter_is_ignored(monkeypatch, working_desktop):
    """The safety guarantee this file exists for: even if something manages
    to put a 'receiver' into parameters, it must never override the
    configured owner - there is no code path that reads it at all."""
    opened, searched = working_desktop
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")

    call_contact.call_contact({"receiver": "Some Random Person"})

    assert searched["query"] == "Eitan"


def test_missing_pyautogui_is_reported_without_crashing(monkeypatch):
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", False)
    monkeypatch.setattr(call_contact, "pyautogui", None)

    result = call_contact.call_contact({})

    assert "pyautogui" in result.lower()


def test_defaults_to_whatsapp_when_platform_omitted(monkeypatch, working_desktop):
    opened, searched = working_desktop
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")

    call_contact.call_contact({})

    assert opened["app"] == "WhatsApp"


def test_telegram_platform_opens_telegram(monkeypatch, working_desktop):
    opened, searched = working_desktop
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")

    call_contact.call_contact({"platform": "telegram"})

    assert opened["app"] == "Telegram"


def test_app_open_failure_is_reported(monkeypatch):
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")
    monkeypatch.setattr(call_contact, "_open_app", lambda name: False)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    result = call_contact.call_contact({})

    assert "could not open" in result.lower()


def test_success_message_never_claims_a_confirmed_connection(monkeypatch, working_desktop):
    """The whole point of this file's honesty note: unlike open_app's
    verified launches, there is no way to confirm a call actually connected -
    the message must not claim otherwise."""
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")

    result = call_contact.call_contact({})

    assert "attempted" in result.lower()
    assert "cannot confirm" in result.lower()


def test_unexpected_exception_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "Eitan")

    def _boom(name):
        raise RuntimeError("boom")
    monkeypatch.setattr(call_contact, "_open_app", _boom)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    result = call_contact.call_contact({})

    assert "could not place the call" in result.lower()


# ── Instagram (browser-based, separate code path from the desktop apps) ─────────

def test_instagram_platform_uses_the_browser_not_open_app(monkeypatch):
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "eitanbb00")
    opened_urls = []
    pasted = []
    called_open_app = {"yes": False}

    monkeypatch.setattr(call_contact, "_open_app", lambda name: called_open_app.__setitem__("yes", True) or True)
    monkeypatch.setattr(call_contact, "_open_browser_url", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr(call_contact, "_paste_text", lambda text: pasted.append(text))
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    result = call_contact.call_contact({"platform": "instagram"})

    assert called_open_app["yes"] is False
    assert opened_urls == ["https://www.instagram.com/direct/new/"]
    assert pasted == ["eitanbb00"]
    assert "instagram" in result.lower()
    assert "attempted" in result.lower()
    assert "cannot confirm" in result.lower()


def test_instagram_browser_open_failure_is_reported(monkeypatch):
    monkeypatch.setattr(call_contact, "get_owner_contact_name", lambda: "eitanbb00")
    monkeypatch.setattr(call_contact, "_open_browser_url", lambda url: False)
    monkeypatch.setattr(call_contact, "pyautogui", SimpleNamespace(
        press=lambda *a, **kw: None, hotkey=lambda *a, **kw: None,
    ))
    monkeypatch.setattr(call_contact, "_PYAUTOGUI", True)

    result = call_contact.call_contact({"platform": "instagram"})

    assert "could not open instagram" in result.lower()


# ── windows_idle_seconds() ───────────────────────────────────────────────────────

def test_windows_idle_seconds_returns_none_when_ctypes_windll_is_unavailable():
    # This test runs on Linux (no ctypes.windll), which is exactly the
    # "any other OS" case the function documents - it must not raise.
    assert call_contact.windows_idle_seconds() is None
