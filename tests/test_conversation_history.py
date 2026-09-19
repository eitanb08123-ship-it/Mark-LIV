"""
memory/conversation_history.py tests. Every test points HISTORY_PATH at a
tmp_path file so nothing touches the real conversation_history.json on
disk, and each test starts from a clean (nonexistent) file.
"""
import threading
import time

import pytest

from memory import conversation_history as ch


@pytest.fixture(autouse=True)
def _isolated_history_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "HISTORY_PATH", tmp_path / "conversation_history.json")


def test_empty_history_returns_no_turns_and_no_prompt_text():
    assert ch.get_recent_turns("whatsapp", "Dana") == []
    assert ch.format_for_prompt("whatsapp", "Dana") == ""
    assert ch.last_handled_incoming("whatsapp", "Dana") == ""


def test_append_and_read_back_a_turn():
    ch.append_turn("whatsapp", "Dana", "them", "Hey, are you free tonight?")

    turns = ch.get_recent_turns("whatsapp", "Dana")

    assert len(turns) == 1
    assert turns[0]["role"] == "them"
    assert turns[0]["text"] == "Hey, are you free tonight?"
    assert "ts" in turns[0]


def test_same_contact_on_different_platforms_does_not_share_a_thread():
    ch.append_turn("whatsapp", "Dana", "them", "WhatsApp message")
    ch.append_turn("instagram", "Dana", "them", "Instagram message")

    assert ch.last_handled_incoming("whatsapp", "Dana") == "WhatsApp message"
    assert ch.last_handled_incoming("instagram", "Dana") == "Instagram message"


def test_different_contacts_on_the_same_platform_do_not_collide():
    ch.append_turn("whatsapp", "Dana", "them", "from Dana")
    ch.append_turn("whatsapp", "Yossi", "them", "from Yossi")

    assert ch.last_handled_incoming("whatsapp", "Dana") == "from Dana"
    assert ch.last_handled_incoming("whatsapp", "Yossi") == "from Yossi"


def test_last_handled_incoming_ignores_jarvis_turns():
    ch.append_turn("whatsapp", "Dana", "them", "question")
    ch.append_turn("whatsapp", "Dana", "jarvis", "answer")

    assert ch.last_handled_incoming("whatsapp", "Dana") == "question"


def test_thread_is_trimmed_to_max_turns():
    for i in range(ch.MAX_TURNS_PER_THREAD + 5):
        ch.append_turn("whatsapp", "Dana", "them", f"msg {i}")

    turns = ch.get_recent_turns("whatsapp", "Dana", limit=1000)

    assert len(turns) == ch.MAX_TURNS_PER_THREAD
    assert turns[-1]["text"] == f"msg {ch.MAX_TURNS_PER_THREAD + 4}"


def test_format_for_prompt_lists_recent_turns_in_order():
    ch.append_turn("whatsapp", "Dana", "them", "Hi")
    ch.append_turn("whatsapp", "Dana", "jarvis", "Hello!")

    formatted = ch.format_for_prompt("whatsapp", "Dana")

    assert "Them: Hi" in formatted
    assert "You (JARVIS): Hello!" in formatted
    assert formatted.index("Hi") < formatted.index("Hello!")


def test_active_conversations_summarizes_every_thread():
    ch.append_turn("whatsapp", "Dana", "them", "hi")
    ch.append_turn("instagram", "Yossi", "jarvis", "on my way")

    convos = {c["contact"]: c for c in ch.active_conversations()}

    assert convos["dana"]["platform"] == "whatsapp"
    assert convos["dana"]["turns"] == 1
    assert convos["yossi"]["platform"] == "instagram"
    assert convos["yossi"]["last_role"] == "jarvis"


def test_append_turn_ignores_empty_text():
    ch.append_turn("whatsapp", "Dana", "them", "")

    assert ch.get_recent_turns("whatsapp", "Dana") == []


def test_load_error_returns_empty_dict_not_raise(tmp_path, monkeypatch):
    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(ch, "HISTORY_PATH", bad_file)

    assert ch.get_recent_turns("whatsapp", "Dana") == []


# ── thread_id keying (item 1/8: a stable ID sidesteps text-equality) ─────────

def test_thread_id_overrides_contact_name_as_the_key():
    """Two different 'contact' labels for the SAME thread_id must share
    one thread - the whole point of carrying a stable ID."""
    ch.append_turn("instagram", "Dana (typo)", "them", "hi", thread_id="t123")
    ch.append_turn("instagram", "Dana", "jarvis", "hello!", thread_id="t123")

    turns = ch.get_recent_turns("instagram", "irrelevant contact name", thread_id="t123")
    assert len(turns) == 2


def test_same_contact_name_different_thread_ids_do_not_collide():
    ch.append_turn("instagram", "Dana", "them", "from thread A", thread_id="tA")
    ch.append_turn("instagram", "Dana", "them", "from thread B", thread_id="tB")

    assert ch.last_handled_incoming("instagram", "Dana", thread_id="tA") == "from thread A"
    assert ch.last_handled_incoming("instagram", "Dana", thread_id="tB") == "from thread B"


def test_thread_id_and_contact_only_keying_are_independent():
    """A row with no thread_id must not leak into / read from one that has
    one for the same contact name - they're deliberately different keys."""
    ch.append_turn("instagram", "Dana", "them", "no id", thread_id=None)
    ch.append_turn("instagram", "Dana", "them", "with id", thread_id="t1")

    assert ch.last_handled_incoming("instagram", "Dana") == "no id"
    assert ch.last_handled_incoming("instagram", "Dana", thread_id="t1") == "with id"


# ── is_duplicate_incoming (item 8: text equality + a time gate) ─────────────

def test_is_duplicate_incoming_true_for_recent_matching_text():
    ch.append_turn("whatsapp", "Dana", "them", "hi")
    assert ch.is_duplicate_incoming("whatsapp", "Dana", "hi") is True


def test_is_duplicate_incoming_false_for_different_text():
    ch.append_turn("whatsapp", "Dana", "them", "hi")
    assert ch.is_duplicate_incoming("whatsapp", "Dana", "bye") is False


def test_is_duplicate_incoming_false_with_no_history():
    assert ch.is_duplicate_incoming("whatsapp", "Dana", "hi") is False


def test_is_duplicate_incoming_false_once_the_gap_has_passed(monkeypatch):
    """The exact bug this exists to fix: Dana says 'hi' again much later -
    that's a NEW message, not the same still-unread row."""
    ch.append_turn("whatsapp", "Dana", "them", "hi")

    # Simulate time passing without a real sleep().
    real_time = time.time
    monkeypatch.setattr(ch.time, "time", lambda: real_time() + 3600)

    assert ch.is_duplicate_incoming("whatsapp", "Dana", "hi", min_gap_seconds=300) is False


def test_is_duplicate_incoming_respects_thread_id():
    ch.append_turn("instagram", "Dana", "them", "hi", thread_id="t1")
    assert ch.is_duplicate_incoming("instagram", "Dana", "hi", thread_id="t1") is True
    assert ch.is_duplicate_incoming("instagram", "Dana", "hi", thread_id="t2") is False


# ── atomicity + concurrency (item 11) ───────────────────────────────────────

def test_append_turn_never_leaves_a_half_written_file(tmp_path):
    ch.append_turn("whatsapp", "Dana", "them", "hi")

    leftovers = list(tmp_path.glob(".conversation_history_*.tmp"))
    assert leftovers == []
    # The real file parses cleanly - a torn/partial write would fail this.
    assert ch.get_recent_turns("whatsapp", "Dana") != []


def test_concurrent_appends_to_different_contacts_do_not_lose_any(monkeypatch):
    """Regression: append_turn() used to _load() (lock, release), mutate
    unlocked, then _save() (lock, release) - a TOCTOU race where two
    concurrent appends could each load the same starting state and the
    second _save() would discard the first's turn. Locking the whole
    read-modify-write in one acquisition fixes this."""
    def _append_many(contact):
        for i in range(15):
            ch.append_turn("whatsapp", contact, "them", f"msg {i}")

    threads = [threading.Thread(target=_append_many, args=(f"contact_{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(8):
        turns = ch.get_recent_turns("whatsapp", f"contact_{i}", limit=1000)
        assert len(turns) == 15
