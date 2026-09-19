"""
actions/instagram_call_answer.py tests. The "detect a ringing call" step is
explicitly an uncalibrated English/Hebrew-language guess (see the module
docstring - Instagram's real incoming-call UI was never inspected live),
so these tests pin what IS verifiable in isolation: the off-by-default
gate, that browser_control being unavailable (e.g. playwright not
installed) is reported rather than crashing, that detection/answering is
scoped to a role="dialog" container (item 4) rather than arbitrary page
text, that ensure_on_inbox() means the actual /direct/inbox/ route (item
5), and that run_cycle() locks the session for its whole sequence (item 6).
"""
import contextlib

import pytest

from actions import instagram_call_answer as ica


class _FakeSession:
    """Stands in for browser_control._BrowserSession. .run(coro) normally
    submits a coroutine to another thread's event loop - here it just
    awaits it inline via asyncio.run(), since these tests don't need real
    concurrency, only the return value."""
    def __init__(self, url="https://www.instagram.com/direct/inbox/",
                dialog_text="", click_result="Could not find element"):
        self.url = url
        self.dialog_text = dialog_text
        self.click_result = click_result
        self.clicked_labels = []
        self.went_to = []
        self.exclusive_entered = False

    def run(self, coro, timeout=15):
        import asyncio
        return asyncio.run(coro)

    @contextlib.contextmanager
    def exclusive(self):
        self.exclusive_entered = True
        yield

    async def get_url(self):
        return self.url

    async def go_to(self, url):
        self.went_to.append(url)
        self.url = url
        return f"Opened: {url}"

    async def role_container_text(self, container_role):
        return self.dialog_text

    async def click_within_role(self, container_role, label):
        self.clicked_labels.append(label)
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
    monkeypatch.setattr(ica, "get_instagram_auto_answer_enabled", lambda: True)
    assert "not available" in ica.run_cycle().lower()


def test_ensure_on_inbox_navigates_when_not_already_there(monkeypatch, available):
    fake = _FakeSession(url="https://example.com")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.ensure_on_inbox()

    assert fake.went_to == [ica._INBOX_URL]
    assert "Opened" in result


def test_ensure_on_inbox_leaves_it_alone_when_exactly_on_the_inbox_route(monkeypatch, available):
    fake = _FakeSession(url="https://www.instagram.com/direct/inbox/")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.ensure_on_inbox()

    assert fake.went_to == []
    assert "already on" in result.lower()


@pytest.mark.parametrize("url", [
    "https://www.instagram.com/direct/t/12345/",   # an open DM thread
    "https://www.instagram.com/",                   # feed
    "https://www.instagram.com/some_profile/",      # profile
    "https://www.instagram.com/reel/abc/",          # reel
    "https://www.instagram.com/accounts/login/",    # login
])
def test_ensure_on_inbox_navigates_for_every_non_inbox_route(monkeypatch, available, url):
    """Item 5's core fix: only the exact inbox route counts as ready - a
    thread/feed/profile/reel/login page used to all pass the old bare
    'instagram.com' check."""
    fake = _FakeSession(url=url)
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    ica.ensure_on_inbox()

    assert fake.went_to == [ica._INBOX_URL]


def test_no_call_indicator_text_means_nothing_happens(monkeypatch, available):
    fake = _FakeSession(dialog_text="Dana: see you tonight! Mom: call me back")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert result == ""
    assert fake.clicked_labels == []


def test_no_dialog_at_all_means_nothing_happens(monkeypatch, available):
    """role_container_text() returns "" when there's no dialog on the page
    at all - must be treated the same as 'nothing ringing', not an error."""
    fake = _FakeSession(dialog_text="")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    assert ica.check_and_answer() == ""


def test_detects_and_answers_a_ringing_call(monkeypatch, available):
    fake = _FakeSession(
        dialog_text="Dana is calling...\nDecline  Accept",
        click_result="Clicked (button): 'Accept'",
    )
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert "Answered" in result
    assert "Accept" in fake.clicked_labels


def test_detects_and_answers_a_ringing_call_in_hebrew(monkeypatch, available):
    fake = _FakeSession(
        dialog_text="דנה מתקשרת...\nדחה  קבל",
        click_result="Clicked (button): 'קבל'",
    )
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert "Answered" in result


def test_call_detected_but_no_button_matches_is_reported_honestly(monkeypatch, available):
    fake = _FakeSession(
        dialog_text="Incoming video call from Dana",
        click_result="Could not find element: 'x'",
    )
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert "needs calibration" in result.lower()
    # every known label was at least attempted
    assert set(fake.clicked_labels) == set(ica._ACCEPT_LABELS)


def test_call_detection_is_scoped_to_the_dialog_not_the_whole_page(monkeypatch, available):
    """Item 4's regression test: an old chat message ('calling you
    later'), a cookie-banner Accept button, or any other page content
    outside the dialog must never trigger this - only
    role_container_text('dialog')'s own text is ever consulted."""
    fake = _FakeSession(dialog_text="")   # nothing IN the dialog looks like a call
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)

    result = ica.check_and_answer()

    assert result == ""
    assert fake.clicked_labels == []


def test_run_cycle_locks_the_session_and_ensures_inbox_then_checks(monkeypatch, available):
    monkeypatch.setattr(ica, "get_instagram_auto_answer_enabled", lambda: True)
    fake = _FakeSession()
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)
    calls = []
    monkeypatch.setattr(ica, "ensure_on_inbox", lambda *a, **kw: calls.append("ensure") or "Already on: x")
    monkeypatch.setattr(ica, "check_and_answer", lambda *a, **kw: calls.append("check") or "")

    ica.run_cycle()

    assert calls == ["ensure", "check"]
    assert fake.exclusive_entered is True


def test_run_cycle_stops_if_navigating_to_the_inbox_fails(monkeypatch, available):
    """Item 5's other requirement: a failed navigation must not fall
    through to scanning/clicking against whatever page is actually up."""
    monkeypatch.setattr(ica, "get_instagram_auto_answer_enabled", lambda: True)
    fake = _FakeSession()
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": fake)
    monkeypatch.setattr(ica, "ensure_on_inbox", lambda *a, **kw: "Could not open Instagram: timeout")
    called = {"check": False}
    monkeypatch.setattr(ica, "check_and_answer", lambda *a, **kw: called.__setitem__("check", True))

    result = ica.run_cycle()

    assert called["check"] is False
    assert "could not open" in result.lower()


def test_check_and_answer_read_failure_is_reported_not_raised(monkeypatch, available):
    class _Boom:
        def run(self, coro, timeout=15):
            raise RuntimeError("session died")
    monkeypatch.setattr(ica, "_get_session", lambda name="chrome": _Boom())

    result = ica.check_and_answer()

    assert "could not read" in result.lower()
