"""
actions/whatsapp_call_answer.py tests. Like its Instagram counterpart, the
"detect a ringing call" step is an uncalibrated guess against WhatsApp's
real UI Automation tree (never inspected live), so these tests pin what IS
verifiable: the off-by-default gate, missing pywinauto, and the actual
polling/click logic against a fake pywinauto-shaped window.
"""
from types import SimpleNamespace

import pytest

from actions import whatsapp_call_answer as wca


def _el(text):
    return SimpleNamespace(window_text=lambda: text)


class _FakeButton:
    def __init__(self, text):
        self._text = text
        self.clicked = False

    def window_text(self):
        return self._text

    def click_input(self):
        self.clicked = True


class _FakeWindow:
    def __init__(self, texts, buttons=None):
        self._texts = texts
        self._buttons = buttons or []

    def descendants(self, control_type=None):
        if control_type == "Button":
            return self._buttons
        return [_el(t) for t in self._texts]


def test_disabled_by_default_does_nothing(monkeypatch):
    monkeypatch.setattr(wca, "get_whatsapp_call_answer_enabled", lambda: False)
    called = {"yes": False}
    monkeypatch.setattr(wca, "check_and_answer", lambda *a, **kw: called.__setitem__("yes", True))

    result = wca.run_cycle()

    assert result == ""
    assert called["yes"] is False


def test_missing_pywinauto_is_reported(monkeypatch):
    monkeypatch.setattr(wca, "_PYWINAUTO", False)
    result = wca.check_and_answer()
    assert "pywinauto" in result.lower()


def test_no_call_indicator_means_nothing_happens(monkeypatch):
    monkeypatch.setattr(wca, "_PYWINAUTO", True)
    fake_win = _FakeWindow(texts=["Dana", "Hey, are you free tonight?"])
    monkeypatch.setattr(wca, "_connect", lambda app_name: fake_win)

    result = wca.check_and_answer()

    assert result == ""


def test_detects_and_answers_a_ringing_call(monkeypatch):
    monkeypatch.setattr(wca, "_PYWINAUTO", True)
    accept_btn = _FakeButton("Accept")
    fake_win = _FakeWindow(
        texts=["Dana is calling...", "Decline", "Accept"],
        buttons=[_FakeButton("Decline"), accept_btn],
    )
    monkeypatch.setattr(wca, "_connect", lambda app_name: fake_win)

    result = wca.check_and_answer()

    assert "Answered" in result
    assert accept_btn.clicked is True


def test_call_detected_but_no_accept_button_is_reported_honestly(monkeypatch):
    monkeypatch.setattr(wca, "_PYWINAUTO", True)
    fake_win = _FakeWindow(texts=["Incoming video call from Dana"], buttons=[])
    monkeypatch.setattr(wca, "_connect", lambda app_name: fake_win)

    result = wca.check_and_answer()

    assert "needs calibration" in result.lower()


def test_connection_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(wca, "_PYWINAUTO", True)

    def _boom(app_name):
        raise RuntimeError("window not found")
    monkeypatch.setattr(wca, "_connect", _boom)

    result = wca.check_and_answer()

    assert "could not read" in result.lower()


def test_run_cycle_calls_check_and_answer_when_enabled(monkeypatch):
    monkeypatch.setattr(wca, "get_whatsapp_call_answer_enabled", lambda: True)
    monkeypatch.setattr(wca, "check_and_answer", lambda app_name="WhatsApp": f"checked {app_name}")

    result = wca.run_cycle()

    assert result == "checked WhatsApp"
