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


@pytest.fixture(autouse=True)
def _no_real_conversation_history(monkeypatch):
    """Isolates every test in this file from the real conversation_history
    module (and the JSON file it would otherwise touch) by default: no
    history, no "already handled" match. Individual tests override
    `is_duplicate_incoming` or `format_for_prompt` to exercise the dedup /
    context-injection paths specifically."""
    monkeypatch.setattr(auto_reply.conversation_history, "format_for_prompt", lambda platform, contact, thread_id=None: "")
    monkeypatch.setattr(auto_reply.conversation_history, "is_duplicate_incoming",
                        lambda platform, contact, text, thread_id=None, min_gap_seconds=300: False)
    monkeypatch.setattr(auto_reply.conversation_history, "replied_too_recently",
                        lambda platform, contact, thread_id=None, min_gap_seconds=15: False)
    monkeypatch.setattr(auto_reply.conversation_history, "DEFAULT_REPLY_COOLDOWN_SECONDS", 15)
    recorded = []
    monkeypatch.setattr(auto_reply.conversation_history, "append_turn",
                        lambda platform, contact, role, text, thread_id=None: recorded.append((platform, contact, role, text)))
    return recorded


@pytest.fixture(autouse=True)
def _default_foreground_ok(monkeypatch):
    """By default, pretend focus can't be checked at all (None) - the
    "proceed with today's best-effort behavior" case - so existing tests
    that don't care about item 3's foreground check aren't affected by it.
    Tests exercising the check itself override this."""
    monkeypatch.setattr(auto_reply, "_ensure_foreground", lambda app_name: None)


@pytest.fixture(autouse=True)
def _no_contact_allow_list(monkeypatch):
    """By default, no allow-list is configured (replies to everyone) - the
    documented default - so existing tests aren't affected. Tests
    exercising the allow-list itself override this."""
    monkeypatch.setattr(auto_reply, "get_auto_reply_contacts", lambda: [])


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


# ── _focus_and_maximize (bring the app to the foreground once, on activation) ─

class _FakeFocusWindow:
    def __init__(self, minimized=False, raise_on=None):
        self.minimized = minimized
        self.restored = False
        self.focused = False
        self.maximized = False
        self._raise_on = raise_on or ()

    def is_minimized(self):
        return self.minimized

    def restore(self):
        if "restore" in self._raise_on:
            raise RuntimeError("restore failed")
        self.restored = True
        self.minimized = False

    def set_focus(self):
        if "set_focus" in self._raise_on:
            raise RuntimeError("set_focus failed")
        self.focused = True

    def maximize(self):
        if "maximize" in self._raise_on:
            raise RuntimeError("maximize failed")
        self.maximized = True


def test_focus_and_maximize_missing_pywinauto_is_reported(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", False)

    result = auto_reply._focus_and_maximize("WhatsApp")

    assert "pywinauto" in result.lower()


def test_focus_and_maximize_restores_focuses_and_maximizes(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    win = _FakeFocusWindow(minimized=True)
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name: win)

    result = auto_reply._focus_and_maximize("WhatsApp")

    assert win.restored is True
    assert win.focused is True
    assert win.maximized is True
    assert "focused and maximized" in result.lower()


def test_focus_and_maximize_skips_restore_when_not_minimized(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    win = _FakeFocusWindow(minimized=False)
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name: win)

    auto_reply._focus_and_maximize("WhatsApp")

    assert win.restored is False
    assert win.focused is True
    assert win.maximized is True


def test_focus_and_maximize_reports_failure_instead_of_raising(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)

    def _boom(app_name):
        raise RuntimeError("window not found")
    monkeypatch.setattr(auto_reply, "_connect", _boom)

    result = auto_reply._focus_and_maximize("WhatsApp")

    assert "could not focus/maximize" in result.lower()


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

    result = auto_reply._generate_reply("whatsapp", "Dana", "שלום, מה קורה?")

    assert result == "OK reply"
    assert "same language as the incoming message" in captured["prompt"]
    assert "שלום, מה קורה?" in captured["prompt"]


def test_generate_reply_includes_recent_conversation_history(monkeypatch):
    """The user's explicit requirement: understand conversation context,
    don't treat every message as a brand new conversation."""
    captured = {}
    monkeypatch.setattr(auto_reply.gemini, "text",
                        lambda prompt, **kw: captured.setdefault("prompt", prompt) or "OK")
    monkeypatch.setattr(auto_reply.conversation_history, "format_for_prompt",
                        lambda platform, contact: "Them: Hi\nYou (JARVIS): Hey!")

    auto_reply._generate_reply("whatsapp", "Dana", "How are you?")

    assert "Them: Hi" in captured["prompt"]
    assert "You (JARVIS): Hey!" in captured["prompt"]
    assert "not a new conversation" in captured["prompt"].lower() or "NOT a new conversation" in captured["prompt"]


def test_validate_reply_rejects_empty_and_caps_length():
    assert auto_reply._validate_reply("") == ""
    assert auto_reply._validate_reply("   ") == ""
    assert auto_reply._validate_reply("  hi  ") == "hi"
    huge = "x" * 5000
    assert len(auto_reply._validate_reply(huge)) == auto_reply._MAX_REPLY_CHARS


def test_reply_to_chat_uses_first_line_of_row_as_contact_and_sends(monkeypatch, _no_real_conversation_history):
    opened = {}
    searched = {}
    pasted = {}

    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "Sure, on it!")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: opened.setdefault("app", name) or True)
    monkeypatch.setattr(auto_reply, "_search_in_app", lambda q, app_name="": searched.setdefault("query", q))
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: pasted.setdefault("text", text))
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name: object())
    monkeypatch.setattr(auto_reply, "_get_window_text", lambda win: "Sure, on it!")

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert opened["app"] == "WhatsApp"
    assert searched["query"] == "Dana"
    assert pasted["text"] == "Sure, on it!"
    assert "Dana" in result
    assert "Sure, on it!" in result
    # both sides of the exchange get recorded for future context/dedup
    assert ("whatsapp", "Dana", "them", "2 unread messages") in _no_real_conversation_history
    assert ("whatsapp", "Dana", "jarvis", "Sure, on it!") in _no_real_conversation_history


def test_reply_to_chat_skips_sending_when_gemini_produces_nothing(monkeypatch):
    called = {"opened": False}
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert called["opened"] is False
    assert "no reply" in result.lower()


def test_reply_to_chat_skips_a_duplicate_of_the_last_handled_message(monkeypatch):
    called = {"opened": False}
    monkeypatch.setattr(auto_reply.conversation_history, "is_duplicate_incoming",
                        lambda platform, contact, text, thread_id=None, min_gap_seconds=300: True)
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert called["opened"] is False
    assert "skipping duplicate" in result.lower()


def test_reply_to_chat_skips_when_replied_too_recently(monkeypatch):
    """Per-chat rate limit (independent of text): even a brand-new, non-
    duplicate incoming message must not be answered again if JARVIS just
    replied to this contact a moment ago - the guard against a runaway
    back-and-forth with another autoresponder."""
    called = {"opened": False}
    monkeypatch.setattr(auto_reply.conversation_history, "replied_too_recently",
                        lambda platform, contact, thread_id=None, min_gap_seconds=15: True)
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\nAre you free tonight?")

    assert called["opened"] is False
    assert "within the last" in result.lower()


def test_reply_to_chat_aborts_when_focus_cannot_be_confirmed(monkeypatch):
    """Item 3: launch_app()/_open_app() only guarantees the process exists,
    not that the correct window has keyboard focus. If _ensure_foreground()
    confirms it does NOT, the reply must not be sent at all."""
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "Sure, on it!")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: True)
    monkeypatch.setattr(auto_reply, "_ensure_foreground", lambda app_name: False)
    pasted = {"called": False}
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: pasted.__setitem__("called", True))

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert pasted["called"] is False
    assert "focus" in result.lower()


def test_reply_to_chat_proceeds_when_focus_cannot_be_checked_at_all(monkeypatch):
    """None means "couldn't verify" (e.g. pywinauto/pywin32 missing) - must
    degrade to best-effort, not block."""
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "Sure, on it!")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: True)
    monkeypatch.setattr(auto_reply, "_ensure_foreground", lambda app_name: None)
    monkeypatch.setattr(auto_reply, "_search_in_app", lambda q, app_name="": None)
    pasted = {"called": False}
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: pasted.__setitem__("called", True))
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert pasted["called"] is True
    assert "focus" not in result.lower()


# ── _split_row (item 2: skip rather than guess, same as Instagram's fix) ────

def test_split_row_returns_the_message_when_structure_looks_trustworthy():
    contact, incoming = auto_reply._split_row("Dana\nAre you free tonight?")
    assert contact == "Dana"
    assert incoming == "Are you free tonight?"


def test_split_row_returns_none_with_no_second_line_at_all():
    contact, incoming = auto_reply._split_row("Dana")
    assert contact == "Dana"
    assert incoming is None


def test_split_row_returns_none_when_the_contact_line_is_itself_a_marker():
    """The row's structure isn't what _find_unread_chats() assumed - what
    got split as "contact" is actually an unread marker/badge, not a name."""
    contact, incoming = auto_reply._split_row("3 unread messages\nDana")
    assert incoming is None


def test_reply_to_chat_skips_when_message_could_not_be_isolated(monkeypatch):
    called = {"opened": False}
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana")

    assert called["opened"] is False
    assert "skipping rather than guessing" in result.lower()


# ── _contact_allowed / per-contact allow-list ───────────────────────────────

def test_contact_allowed_true_when_no_allow_list_is_configured(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_contacts", lambda: [])
    assert auto_reply._contact_allowed("Dana") is True


@pytest.mark.parametrize("allow_list,contact,expected", [
    (["Mom"], "Mom", True),
    (["Mom"], "mom", True),          # case-insensitive
    (["mom"], "Mom", True),          # case-insensitive the other way
    (["Mom"], "Dana", False),        # not on the list
    (["Mom", "Dana"], "Dana", True),
    ([], "Dana", True),              # empty list = everyone allowed
    (["Mom"], "Mommy", False),       # no fuzzy matching, deliberately
    (["Mom"], "Mo", False),
])
def test_contact_allowed_matching(monkeypatch, allow_list, contact, expected):
    monkeypatch.setattr(auto_reply, "get_auto_reply_contacts", lambda: allow_list)
    assert auto_reply._contact_allowed(contact) is expected


def test_reply_to_chat_skips_a_contact_not_on_the_allow_list(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_contacts", lambda: ["Mom"])
    called = {"generated": False}
    monkeypatch.setattr(auto_reply, "_generate_reply",
                        lambda platform, contact, text: called.__setitem__("generated", True) or "hi")
    called_open = {"opened": False}
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called_open.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\nAre you free tonight?")

    assert called["generated"] is False    # never even asked Gemini for a reply
    assert called_open["opened"] is False
    assert "not on the auto-reply allow-list" in result.lower()


def test_reply_to_chat_replies_to_a_contact_on_the_allow_list(monkeypatch, _no_real_conversation_history):
    monkeypatch.setattr(auto_reply, "get_auto_reply_contacts", lambda: ["Mom", "Dana"])
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "Sure, on it!")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: True)
    monkeypatch.setattr(auto_reply, "_search_in_app", lambda q, app_name="": None)
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: None)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name: object())
    monkeypatch.setattr(auto_reply, "_get_window_text", lambda win: "Sure, on it!")

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\nAre you free tonight?")

    assert result.startswith("Replied")


def test_reply_to_instagram_row_skips_a_contact_not_on_the_allow_list(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_contacts", lambda: ["Mom"])
    called = {"generated": False}
    monkeypatch.setattr(auto_reply, "_generate_reply",
                        lambda platform, contact, text: called.__setitem__("generated", True) or "hi")
    fake = _FakeBrowserSession()
    row = {"contact": "Dana", "incoming_text": "you around?", "thread_id": "t1"}

    result = auto_reply._reply_to_instagram_row(fake, row)

    assert called["generated"] is False
    assert fake.clicked == []
    assert "not on the auto-reply allow-list" in result.lower()


def test_reply_to_instagram_row_replies_to_a_contact_on_the_allow_list(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_contacts", lambda: ["mom"])   # different casing
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "On my way!")
    fake = _FakeBrowserSession()
    row = {"contact": "Mom", "incoming_text": "you around?", "thread_id": "t1"}

    result = auto_reply._reply_to_instagram_row(fake, row)

    assert result.startswith("Replied")


# ── post-send verification (item 4: confirm it actually landed) ────────────

def test_reply_to_chat_does_not_save_history_on_unconfirmed_delivery(monkeypatch, _no_real_conversation_history):
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "Sure, on it!")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: True)
    monkeypatch.setattr(auto_reply, "_search_in_app", lambda q, app_name="": None)
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: None)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name: object())
    monkeypatch.setattr(auto_reply, "_get_window_text", lambda win: "totally unrelated window content")

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert "could not verify" in result.lower()
    assert _no_real_conversation_history == []  # neither side of the exchange was saved


def test_reply_to_chat_verification_survives_a_read_failure(monkeypatch, _no_real_conversation_history):
    """If reading the window back fails outright (e.g. pywinauto not
    installed, the app closed), that must be treated as unverified - not
    raise and crash the whole poll cycle."""
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "Sure, on it!")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: True)
    monkeypatch.setattr(auto_reply, "_search_in_app", lambda q, app_name="": None)
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: None)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))

    def _boom(app_name):
        raise RuntimeError("window not found")
    monkeypatch.setattr(auto_reply, "_connect", _boom)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert "could not verify" in result.lower()


# ── dry-run / shadow mode ────────────────────────────────────────────────────

def test_reply_to_chat_dry_run_never_touches_the_desktop(monkeypatch, _no_real_conversation_history):
    monkeypatch.setattr(auto_reply, "get_auto_reply_dry_run", lambda: True)
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "Sure, on it!")
    called = {"opened": False}
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert called["opened"] is False
    assert result.startswith("[DRY RUN]")
    assert "Dana" in result and "Sure, on it!" in result
    # still recorded, so dedup/cooldown keep working across dry-run cycles
    assert ("whatsapp", "Dana", "them", "2 unread messages") in _no_real_conversation_history
    assert ("whatsapp", "Dana", "jarvis", "Sure, on it!") in _no_real_conversation_history


def test_reply_to_instagram_row_dry_run_never_clicks(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_dry_run", lambda: True)
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "On my way!")
    fake = _FakeBrowserSession()
    row = {"contact": "Dana", "incoming_text": "you around?", "thread_id": "t1"}

    result = auto_reply._reply_to_instagram_row(fake, row)

    assert fake.clicked == []
    assert result.startswith("[DRY RUN]")
    assert "Dana" in result and "On my way!" in result


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


# ── per-cycle send cap (item 3): no burst of messages after a long absence ──

def test_auto_reply_cycle_desktop_caps_replies_per_cycle_and_defers_the_rest(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", True)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    rows = [f"Contact{i}\nunread" for i in range(auto_reply._MAX_REPLIES_PER_CYCLE + 3)]
    monkeypatch.setattr(auto_reply, "_find_unread_chats", lambda app: rows)
    monkeypatch.setattr(auto_reply, "_reply_to_chat",
                        lambda app, row: f"Replied to {row.splitlines()[0]} via WhatsApp: hi")

    result = auto_reply.auto_reply_cycle("WhatsApp")

    replied = [r for r in result if r.startswith("Replied")]
    assert len(replied) == auto_reply._MAX_REPLIES_PER_CYCLE
    assert any("deferred to the next poll" in r for r in result)
    assert "3 more" in result[-1]


def test_auto_reply_cycle_desktop_sleeps_between_consecutive_real_sends(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", True)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    monkeypatch.setattr(auto_reply, "_find_unread_chats", lambda app: ["Dana\nunread", "Yossi\nunread"])
    monkeypatch.setattr(auto_reply, "_reply_to_chat",
                        lambda app, row: f"Replied to {row.splitlines()[0]} via WhatsApp: hi")
    sleeps = []
    monkeypatch.setattr(auto_reply.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(auto_reply.random, "uniform", lambda a, b: 2.5)

    auto_reply.auto_reply_cycle("WhatsApp")

    assert sleeps == [2.5]  # one pause between 2 sends, none needed after the last


def test_auto_reply_cycle_desktop_no_deferred_message_when_everything_fits(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_PYAUTOGUI", True)
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    monkeypatch.setattr(auto_reply, "_find_unread_chats", lambda app: ["Dana\nunread"])
    monkeypatch.setattr(auto_reply, "_reply_to_chat", lambda app, row: "Replied to Dana via WhatsApp: hi")

    result = auto_reply.auto_reply_cycle("WhatsApp")

    assert not any("deferred" in r for r in result)


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


def test_find_unread_chats_matches_hebrew_unread_markers(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    fake_rows = [
        SimpleNamespace(window_text=lambda: "דנה\nהודעה אחת שלא נקראה"),
        SimpleNamespace(window_text=lambda: "אמא\nנתראה הערב"),
        SimpleNamespace(window_text=lambda: "קבוצת עבודה\n2 הודעות שלא נקראו"),
    ]
    fake_win = SimpleNamespace(descendants=lambda control_type: fake_rows)
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name, timeout=5.0: fake_win)

    result = auto_reply._find_unread_chats("WhatsApp")

    assert result == ["דנה\nהודעה אחת שלא נקראה", "קבוצת עבודה\n2 הודעות שלא נקראו"]


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


def test_inspect_chat_window_includes_a_privacy_note(monkeypatch):
    """Item 7: the dump can include real chat content, not just structure
    - callers must be warned, not left to assume it's safe to share."""
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    fake_win = SimpleNamespace(print_control_identifiers=lambda depth=3: print("Dialog"))
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name, timeout=5.0: fake_win)

    result = auto_reply.inspect_chat_window("WhatsApp")

    assert "privacy" in result.lower()


# ── health_check() (item 7) ──────────────────────────────────────────────────

def test_health_check_desktop_ok_when_rows_are_found(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    fake_win = SimpleNamespace(descendants=lambda control_type=None: [SimpleNamespace(window_text=lambda: "Dana")])
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name, timeout=5.0: fake_win)

    assert auto_reply.health_check("whatsapp") == "ok"


def test_health_check_desktop_warns_when_nothing_is_found(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)
    fake_win = SimpleNamespace(descendants=lambda control_type=None: [])
    monkeypatch.setattr(auto_reply, "_connect", lambda app_name, timeout=5.0: fake_win)

    result = auto_reply.health_check("whatsapp")

    assert "warning" in result.lower()


def test_health_check_desktop_reports_unavailable_without_pywinauto(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", False)

    assert "unavailable" in auto_reply.health_check("whatsapp").lower()


def test_health_check_desktop_reports_connection_failure(monkeypatch):
    monkeypatch.setattr(auto_reply, "_PYWINAUTO", True)

    def _boom(app_name, timeout=5.0):
        raise RuntimeError("not open")
    monkeypatch.setattr(auto_reply, "_connect", _boom)

    assert "unavailable" in auto_reply.health_check("whatsapp").lower()


def test_health_check_instagram_reports_unavailable_without_browser_control(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", False)

    assert "unavailable" in auto_reply.health_check("instagram").lower()


# ── Instagram path (browser-based, reuses browser_control's Playwright session) ──
# Regression coverage for the actual bug reported: auto_reply defaulted to
# watching WhatsApp even for a setup built entirely around a dedicated
# Instagram account, so enabling it silently watched the wrong app.

class _FakeBrowserSession:
    def __init__(self, url="https://www.instagram.com/direct/inbox/", page_text="",
                rows=None, click_ok=True, type_ok=True, press_ok=True, delivered=True):
        self.url = url
        self.page_text = page_text
        self.rows = rows if rows is not None else []
        self.went_to = []
        self.clicked = []
        self.typed = []
        self.pressed = []
        # Individually toggle each action's success, for item 2's
        # verify-every-step tests.
        self.click_ok = click_ok
        self.type_ok = type_ok
        self.press_ok = press_ok
        self.delivered = delivered

    def run(self, coro, timeout=15):
        import asyncio
        return asyncio.run(coro)

    def exclusive(self):
        import contextlib
        @contextlib.contextmanager
        def _cm():
            yield
        return _cm()

    async def get_url(self):
        return self.url

    async def get_text(self):
        return self.page_text

    async def go_to(self, url):
        self.went_to.append(url)
        self.url = url
        return f"Opened: {url}"

    async def query_rows(self, selector, text_selector='[dir="auto"]', limit=30):
        return self.rows

    async def smart_click(self, description):
        self.clicked.append(description)
        if not self.click_ok:
            return f"Could not find element: '{description}'"
        return f"Clicked: '{description}'"

    async def smart_type(self, description, text):
        self.typed.append((description, text))
        if not self.type_ok:
            return f"Could not find input: '{description}'"
        if self.delivered:
            self.page_text = (self.page_text or "") + "\n" + text
        return f"Typed into ({description}): '{text}'"

    async def press(self, key):
        self.pressed.append(key)
        if not self.press_ok:
            return f"Key error: boom"
        return f"Pressed: {key}"


def test_auto_reply_defaults_to_the_configured_platforms_not_whatsapp(monkeypatch):
    """The exact bug: calling with no explicit platforms must honor
    get_auto_reply_platforms() (["instagram"], by default now) instead of
    silently falling back to WhatsApp."""
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "get_auto_reply_platforms", lambda: ["instagram"])
    called = {"instagram": False}
    monkeypatch.setattr(auto_reply, "_auto_reply_cycle_instagram", lambda: called.__setitem__("instagram", True) or [])

    auto_reply.auto_reply_cycle()

    assert called["instagram"] is True


def test_auto_reply_watches_multiple_platforms_at_once(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "get_auto_reply_platforms", lambda: ["whatsapp", "instagram"])
    called = []
    monkeypatch.setattr(auto_reply, "_auto_reply_cycle_desktop_app",
                        lambda platform: called.append(("desktop", platform)) or [f"desktop:{platform}"])
    monkeypatch.setattr(auto_reply, "_auto_reply_cycle_instagram",
                        lambda: called.append(("instagram",)) or ["instagram:done"])

    result = auto_reply.auto_reply_cycle()

    assert ("desktop", "whatsapp") in called
    assert ("instagram",) in called
    assert result == ["desktop:whatsapp", "instagram:done"]


def test_instagram_platform_reports_when_browser_control_unavailable(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", False)

    result = auto_reply.auto_reply_cycle("instagram")

    assert any("browser_control" in r.lower() for r in result)


def _row(name, preview=None, marker="3 unread messages", href="https://instagram.com/direct/t/1/"):
    """A query_rows()-shaped row. 3 texts (name, preview, marker) is the
    'confident' case; omitting `preview` yields the low-confidence 2-node
    case (today's exact bug: name + marker only, no real message)."""
    texts = [name] + ([preview] if preview is not None else []) + [marker]
    return {"href": href, "texts": texts}


def test_health_check_instagram_ok_when_rows_are_found(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("Dana", preview="hi")])
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    assert auto_reply.health_check("instagram") == "ok"


def test_health_check_instagram_warns_when_no_rows_found(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[])
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    assert "warning" in auto_reply.health_check("instagram").lower()


def test_find_unread_instagram_chats_extracts_the_real_message_not_the_marker(monkeypatch):
    """Item 1's core fix: the actual message preview (not '3 unread
    messages') must be what gets extracted."""
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("Dana", preview="Are you free tonight?")])

    result = auto_reply._find_unread_instagram_chats(fake)

    assert result == [{
        "contact": "Dana", "incoming_text": "Are you free tonight?",
        "thread_id": "https://instagram.com/direct/t/1/",
    }]


def test_find_unread_instagram_chats_ignores_rows_without_an_unread_marker(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[{"href": "t2", "texts": ["Mom", "See you tonight"]}])

    result = auto_reply._find_unread_instagram_chats(fake)

    assert result == []


def test_find_unread_instagram_chats_low_confidence_row_has_no_incoming_text(monkeypatch):
    """The exact bug: only a name + the unread-count marker, no separate
    preview text - must NOT guess the marker is the real message."""
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("Dana", preview=None)])

    result = auto_reply._find_unread_instagram_chats(fake)

    assert len(result) == 1
    assert result[0]["contact"] == "Dana"
    assert result[0]["incoming_text"] is None


def test_find_unread_instagram_chats_matches_hebrew_unread_markers(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("דנה", preview="נתראה הערב?", marker="3 הודעות שלא נקראו")])

    result = auto_reply._find_unread_instagram_chats(fake)

    assert result[0]["contact"] == "דנה"
    assert result[0]["incoming_text"] == "נתראה הערב?"


def test_find_unread_instagram_chats_navigates_when_not_on_inbox_route(monkeypatch):
    """Item 5's route check reused here: a non-inbox URL (profile, feed,
    thread, login - not just 'not instagram.com at all') must navigate."""
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(url="https://www.instagram.com/some_profile/", rows=[])

    auto_reply._find_unread_instagram_chats(fake)

    assert fake.went_to == [auto_reply._INSTAGRAM_INBOX_URL]


def test_find_unread_instagram_chats_no_navigation_when_already_on_inbox(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(url="https://www.instagram.com/direct/inbox/", rows=[])

    auto_reply._find_unread_instagram_chats(fake)

    assert fake.went_to == []


# ── _reply_via_instagram (item 2: verify every action + delivery) ──────────

def test_reply_via_instagram_full_success_path(monkeypatch):
    fake = _FakeBrowserSession()

    result = auto_reply._reply_via_instagram(fake, "Dana", "Sure, on it!")

    assert fake.clicked == ["Dana"]
    assert fake.typed == [("Message", "Sure, on it!")]
    assert fake.pressed == ["Enter"]
    assert result.startswith("Replied")
    assert "Dana" in result and "Sure, on it!" in result


def test_reply_via_instagram_stops_if_click_fails(monkeypatch):
    fake = _FakeBrowserSession(click_ok=False)

    result = auto_reply._reply_via_instagram(fake, "Dana", "Sure, on it!")

    assert fake.typed == []
    assert fake.pressed == []
    assert not result.startswith("Replied")
    assert "open the conversation" in result.lower()


def test_reply_via_instagram_stops_if_typing_fails(monkeypatch):
    fake = _FakeBrowserSession(type_ok=False)

    result = auto_reply._reply_via_instagram(fake, "Dana", "Sure, on it!")

    assert fake.pressed == []
    assert not result.startswith("Replied")
    assert "message box" in result.lower()


def test_reply_via_instagram_stops_if_pressing_enter_fails(monkeypatch):
    fake = _FakeBrowserSession(press_ok=False)

    result = auto_reply._reply_via_instagram(fake, "Dana", "Sure, on it!")

    assert not result.startswith("Replied")
    assert "submit" in result.lower()


def test_reply_via_instagram_reports_unconfirmed_delivery(monkeypatch):
    """A click/type/press can each individually 'succeed' while the
    message still never reaches the conversation - delivery must be
    verified by re-reading the page, not assumed."""
    fake = _FakeBrowserSession(delivered=False)

    result = auto_reply._reply_via_instagram(fake, "Dana", "Sure, on it!")

    assert not result.startswith("Replied")
    assert "could not verify" in result.lower()


# ── _reply_to_instagram_row / _auto_reply_cycle_instagram (items 1, 2, 6, 8) ─

def test_reply_to_instagram_row_skips_when_message_could_not_be_isolated(monkeypatch):
    """Item 1's explicit fallback: never guess - skip and say so."""
    fake = _FakeBrowserSession()
    row = {"contact": "Dana", "incoming_text": None, "thread_id": "t1"}
    gemini_called = {"yes": False}
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda *a: gemini_called.__setitem__("yes", True) or "x")

    result = auto_reply._reply_to_instagram_row(fake, row)

    assert gemini_called["yes"] is False
    assert fake.clicked == []
    assert "skipping" in result.lower()


def test_auto_reply_cycle_instagram_end_to_end(monkeypatch, _no_real_conversation_history):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("Dana", preview="unread")])
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "On my way!")

    result = auto_reply.auto_reply_cycle("instagram")

    assert len(result) == 1
    assert "Dana" in result[0]
    assert fake.typed == [("Message", "On my way!")]
    assert ("instagram", "Dana", "them", "unread") in _no_real_conversation_history
    assert ("instagram", "Dana", "jarvis", "On my way!") in _no_real_conversation_history


def test_auto_reply_cycle_instagram_skips_a_duplicate_message(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("Dana", preview="unread")])
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)
    monkeypatch.setattr(auto_reply.conversation_history, "is_duplicate_incoming",
                        lambda platform, contact, text, thread_id=None, min_gap_seconds=300: True)

    result = auto_reply.auto_reply_cycle("instagram")

    assert "skipping duplicate" in result[0].lower()
    assert fake.clicked == []


def test_auto_reply_cycle_instagram_skips_when_replied_too_recently(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("Dana", preview="a brand new different message")])
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)
    monkeypatch.setattr(auto_reply.conversation_history, "replied_too_recently",
                        lambda platform, contact, thread_id=None, min_gap_seconds=15: True)

    result = auto_reply.auto_reply_cycle("instagram")

    assert "within the last" in result[0].lower()
    assert fake.clicked == []


def test_auto_reply_cycle_instagram_does_not_save_history_on_unconfirmed_delivery(monkeypatch, _no_real_conversation_history):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[_row("Dana", preview="unread")], delivered=False)
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "On my way!")

    result = auto_reply.auto_reply_cycle("instagram")

    assert "could not verify" in result[0].lower()
    assert _no_real_conversation_history == []


def test_auto_reply_cycle_instagram_locks_the_session_for_the_whole_cycle(monkeypatch):
    """Item 6: the find -> reply sequence must run inside session.exclusive()."""
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(rows=[])
    entered = {"yes": False}
    real_exclusive = fake.exclusive

    def _tracking_exclusive():
        entered["yes"] = True
        return real_exclusive()

    fake.exclusive = _tracking_exclusive
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    auto_reply.auto_reply_cycle("instagram")

    assert entered["yes"] is True


def test_auto_reply_cycle_instagram_caps_replies_per_cycle_and_defers_the_rest(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    n = auto_reply._MAX_REPLIES_PER_CYCLE + 2
    rows = [_row(f"Contact{i}", preview="unread", href=f"https://instagram.com/direct/t/{i}/") for i in range(n)]
    fake = _FakeBrowserSession(rows=rows)
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "On my way!")

    result = auto_reply.auto_reply_cycle("instagram")

    replied = [r for r in result if r.startswith("Replied")]
    assert len(replied) == auto_reply._MAX_REPLIES_PER_CYCLE
    assert any("deferred to the next poll" in r for r in result)
    assert "2 more" in result[-1]
