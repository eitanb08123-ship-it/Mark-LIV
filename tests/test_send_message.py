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
