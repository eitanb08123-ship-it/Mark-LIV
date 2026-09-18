"""
memory/conversation_history.py tests. Every test points HISTORY_PATH at a
tmp_path file so nothing touches the real conversation_history.json on
disk, and each test starts from a clean (nonexistent) file.
"""
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
