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
    `last_handled_incoming` or `format_for_prompt` to exercise the dedup /
    context-injection paths specifically."""
    monkeypatch.setattr(auto_reply.conversation_history, "format_for_prompt", lambda platform, contact: "")
    monkeypatch.setattr(auto_reply.conversation_history, "last_handled_incoming", lambda platform, contact: "")
    recorded = []
    monkeypatch.setattr(auto_reply.conversation_history, "append_turn",
                        lambda platform, contact, role, text: recorded.append((platform, contact, role, text)))
    return recorded


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
    monkeypatch.setattr(auto_reply, "_search_in_app", lambda q: searched.setdefault("query", q))
    monkeypatch.setattr(auto_reply, "_paste_text", lambda text: pasted.setdefault("text", text))
    monkeypatch.setattr(auto_reply, "pyautogui", SimpleNamespace(press=lambda *a, **kw: None))

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
    monkeypatch.setattr(auto_reply.conversation_history, "last_handled_incoming",
                        lambda platform, contact: "2 unread messages")
    monkeypatch.setattr(auto_reply, "_open_app", lambda name: called.__setitem__("opened", True) or True)

    result = auto_reply._reply_to_chat("WhatsApp", "Dana\n2 unread messages")

    assert called["opened"] is False
    assert "skipping duplicate" in result.lower()


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


# ── Instagram path (browser-based, reuses browser_control's Playwright session) ──
# Regression coverage for the actual bug reported: auto_reply defaulted to
# watching WhatsApp even for a setup built entirely around a dedicated
# Instagram account, so enabling it silently watched the wrong app.

class _FakeBrowserSession:
    def __init__(self, url="https://www.instagram.com/direct/inbox/", page_text=""):
        self.url = url
        self.page_text = page_text
        self.went_to = []
        self.clicked = []
        self.typed = []
        self.pressed = []

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
        self.clicked.append(description)
        return f"Clicked: '{description}'"

    async def smart_type(self, description, text):
        self.typed.append((description, text))
        return f"Typed into ({description}): '{text}'"

    async def press(self, key):
        self.pressed.append(key)
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


def test_find_unread_instagram_chats_pairs_contact_with_the_unread_marker(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(page_text="Dana\n3 unread messages\nMom\nSee you tonight")
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    result = auto_reply._find_unread_instagram_chats()

    # "Mom" has no unread marker and must not be picked up; "Dana" (the line
    # right before the unread marker) must be preserved as the contact -
    # this is the exact bug caught while writing this test: matching only
    # the marker line on its own loses the contact entirely.
    assert result == ["Dana\n3 unread messages"]
    assert fake.went_to == []  # already on instagram.com - no navigation needed


def test_find_unread_instagram_chats_matches_hebrew_unread_markers(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(page_text="דנה\n3 הודעות שלא נקראו\nאמא\nנתראה הערב")
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    result = auto_reply._find_unread_instagram_chats()

    assert result == ["דנה\n3 הודעות שלא נקראו"]


def test_find_unread_instagram_chats_navigates_when_not_on_instagram(monkeypatch):
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    fake = _FakeBrowserSession(url="https://example.com", page_text="")
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    auto_reply._find_unread_instagram_chats()

    assert fake.went_to == [auto_reply._INSTAGRAM_INBOX_URL]


def test_reply_via_instagram_clicks_types_and_sends(monkeypatch):
    fake = _FakeBrowserSession()
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    result = auto_reply._reply_via_instagram("Dana", "Sure, on it!")

    assert fake.clicked == ["Dana"]
    assert fake.typed == [("Message", "Sure, on it!")]
    assert fake.pressed == ["Enter"]
    assert "Dana" in result
    assert "Sure, on it!" in result


def test_auto_reply_cycle_instagram_end_to_end(monkeypatch, _no_real_conversation_history):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    monkeypatch.setattr(auto_reply, "_find_unread_instagram_chats", lambda: ["Dana\nunread"])
    monkeypatch.setattr(auto_reply, "_generate_reply", lambda platform, contact, text: "On my way!")
    fake = _FakeBrowserSession()
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    result = auto_reply.auto_reply_cycle("instagram")

    assert len(result) == 1
    assert "Dana" in result[0]
    assert fake.typed == [("Message", "On my way!")]
    assert ("instagram", "Dana", "them", "unread") in _no_real_conversation_history
    assert ("instagram", "Dana", "jarvis", "On my way!") in _no_real_conversation_history


def test_auto_reply_cycle_instagram_skips_a_duplicate_message(monkeypatch):
    monkeypatch.setattr(auto_reply, "get_auto_reply_enabled", lambda: True)
    monkeypatch.setattr(auto_reply, "_BROWSER_CONTROL_AVAILABLE", True)
    monkeypatch.setattr(auto_reply, "_find_unread_instagram_chats", lambda: ["Dana\nunread"])
    monkeypatch.setattr(auto_reply.conversation_history, "last_handled_incoming",
                        lambda platform, contact: "unread")
    fake = _FakeBrowserSession()
    monkeypatch.setattr(auto_reply, "_get_browser_session", lambda name="chrome": fake)

    result = auto_reply.auto_reply_cycle("instagram")

    assert "skipping duplicate" in result[0].lower()
    assert fake.clicked == []
