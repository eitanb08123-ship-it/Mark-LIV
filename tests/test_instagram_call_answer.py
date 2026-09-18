"""
actions/instagram_call_answer.py tests. The "detect a ringing call" step is
explicitly an uncalibrated English-language guess (see the module
docstring - Instagram's real incoming-call UI was never inspected live),
so these tests pin what IS verifiable in isolation: the off-by-default
gate, that browser_control being unavailable (e.g. playwright not
installed) is reported rather than crashing, and the actual polling logic
against a fake session object.
"""
from types import SimpleNamespace

import pytest

from actions import instagram_call_answer as ica


class _FakeSession:
    """Stands in for browser_control._BrowserSession. .run(coro) normally
    submits a coroutine to another thread's event loop - here it just
    awaits it inline via asyncio.run(), since these tests don't need real
    concurrency, only the return value."""
    def __init__(self, url="https://www.instagram.com/direct/inbox/",
                page_text="", click_result="Could not find element"):
        self.url = url
        self.page_text = page_text
        self.click_result = click_result
        self.clicked_labels = []
        self.went_to = []

    def run(self, coro, timeout=15):
        import asyncio
        return asyncio.run(coro)

    async def get_url(self):
        return self.url

    async def get_text(self):
        return self.page_text

    async def go_to(self, url):
        self.went_to.append(url)
        self.url = url
        return f"Opened: {url}"

    async def smart_click(self, description):
        self.clicked_labels.append(description)
        return self.click_result


@pytest.fixture
def available(monkeypatch):
    monkeypatch.setattr(ica, "_BROWSER_CONTROL_AVAILABLE", True)


def test_disabled_by_default_does_nothing(monkeypatch, available):
    monkeypatch.setattr(ica, "get_instagram_auto_answer_enabled", lambda: False)
    called = {"yes": False}
    monkeypatch.setattr(ica, "ensure_on_inbox", lambda *a, **kw: called.__setitem__("yes", True))

    result = ica.run_cycle()

    assert result == ""
    assert called["yes"] is False


def test_unavailable_browser_control_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(ica, "_BROWSER_CONTROL_AVAILABLE", False)

    assert "not available" in ica.ensure_on_inbox().lower()
    assert "not available" in ica.check_and_answer().lower()


def test_ensure_on_inbox_navigates_when_not_already_there(monkeypatch, available):
    fake = _FakeSession(url="https://example.com")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.ensure_on_inbox()

    assert fake.went_to == [ica._INBOX_URL]
    assert "Opened" in result


def test_ensure_on_inbox_leaves_it_alone_when_already_on_instagram(monkeypatch, available):
    fake = _FakeSession(url="https://www.instagram.com/direct/t/12345/")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.ensure_on_inbox()

    assert fake.went_to == []
    assert "already on" in result.lower()


def test_no_call_indicator_text_means_nothing_happens(monkeypatch, available):
    fake = _FakeSession(page_text="Dana: see you tonight! Mom: call me back")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert result == ""
    assert fake.clicked_labels == []


def test_detects_and_answers_a_ringing_call(monkeypatch, available):
    fake = _FakeSession(
        page_text="Dana is calling...\nDecline  Accept",
        click_result="Clicked (button): 'Accept'",
    )
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert "Answered" in result
    assert "Accept" in fake.clicked_labels


def test_call_detected_but_no_button_matches_is_reported_honestly(monkeypatch, available):
    fake = _FakeSession(
        page_text="Incoming video call from Dana",
        click_result="Could not find element: 'x'",
    )
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert "needs calibration" in result.lower()
    # every known label was at least attempted
    assert set(fake.clicked_labels) == set(ica._ACCEPT_LABELS)


def test_run_cycle_ensures_inbox_then_checks_for_a_call(monkeypatch, available):
    monkeypatch.setattr(ica, "get_instagram_auto_answer_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(ica, "ensure_on_inbox", lambda *a, **kw: calls.append("ensure") or "ok")
    monkeypatch.setattr(ica, "check_and_answer", lambda *a, **kw: calls.append("check") or "")

    ica.run_cycle()

    assert calls == ["ensure", "check"]


def test_check_and_answer_read_failure_is_reported_not_raised(monkeypatch, available):
    class _Boom:
        def run(self, coro, timeout=15):
            raise RuntimeError("session died")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": _Boom())

    result = ica.check_and_answer()

    assert "could not read" in result.lower()
