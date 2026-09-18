"""
actions/auto_reply_settings.py tests — the tool JARVIS calls from a normal
conversation to turn auto-reply / call auto-answer on or off. Pins: each
flag is only touched when its parameter is actually passed, invalid
platform names are dropped rather than accepted verbatim, an empty call
just reports status without changing anything, and the reported status
always reflects config_manager's real (possibly monkeypatched) state.
"""
from actions import auto_reply_settings as settings


def _config_state(monkeypatch, **overrides):
    state = {
        "auto_reply_enabled": False,
        "auto_reply_platforms": ["instagram"],
        "whatsapp_call_answer_enabled": False,
        "instagram_auto_answer_enabled": False,
    }
    state.update(overrides)

    monkeypatch.setattr(settings, "get_auto_reply_enabled", lambda: state["auto_reply_enabled"])
    monkeypatch.setattr(settings, "get_auto_reply_platforms", lambda: state["auto_reply_platforms"])
    monkeypatch.setattr(settings, "get_whatsapp_call_answer_enabled", lambda: state["whatsapp_call_answer_enabled"])
    monkeypatch.setattr(settings, "get_instagram_auto_answer_enabled", lambda: state["instagram_auto_answer_enabled"])

    monkeypatch.setattr(settings, "save_auto_reply_enabled",
                         lambda v: state.__setitem__("auto_reply_enabled", v))
    monkeypatch.setattr(settings, "save_auto_reply_platforms",
                         lambda v: state.__setitem__("auto_reply_platforms", v))
    monkeypatch.setattr(settings, "save_whatsapp_call_answer_enabled",
                         lambda v: state.__setitem__("whatsapp_call_answer_enabled", v))
    monkeypatch.setattr(settings, "save_instagram_auto_answer_enabled",
                         lambda v: state.__setitem__("instagram_auto_answer_enabled", v))
    return state


def test_no_parameters_only_reports_status_and_changes_nothing(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_enabled=True, auto_reply_platforms=["whatsapp"])

    result = settings.configure_auto_response({})

    assert "Current settings" in result
    assert "whatsapp" in result
    assert state == {
        "auto_reply_enabled": True,
        "auto_reply_platforms": ["whatsapp"],
        "whatsapp_call_answer_enabled": False,
        "instagram_auto_answer_enabled": False,
    }


def test_enables_auto_reply_and_sets_platforms(monkeypatch):
    state = _config_state(monkeypatch)

    result = settings.configure_auto_response({
        "auto_reply_enabled": True,
        "auto_reply_platforms": ["whatsapp", "instagram"],
    })

    assert state["auto_reply_enabled"] is True
    assert state["auto_reply_platforms"] == ["whatsapp", "instagram"]
    assert "turned on" in result


def test_invalid_platform_names_are_dropped(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_platforms=["instagram"])

    settings.configure_auto_response({"auto_reply_platforms": ["snapchat", "whatsapp"]})

    assert state["auto_reply_platforms"] == ["whatsapp"]


def test_platforms_alone_without_any_valid_entry_does_not_touch_config(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_platforms=["instagram"])

    settings.configure_auto_response({"auto_reply_platforms": ["snapchat"]})

    assert state["auto_reply_platforms"] == ["instagram"]


def test_enables_whatsapp_and_instagram_call_answer_independently(monkeypatch):
    state = _config_state(monkeypatch)

    result = settings.configure_auto_response({
        "whatsapp_call_answer_enabled": True,
        "instagram_call_answer_enabled": False,
    })

    assert state["whatsapp_call_answer_enabled"] is True
    assert state["instagram_auto_answer_enabled"] is False
    assert "WhatsApp call auto-answer turned on" in result


def test_disabling_a_feature_is_reported_too(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_enabled=True)

    result = settings.configure_auto_response({"auto_reply_enabled": False})

    assert state["auto_reply_enabled"] is False
    assert "turned off" in result


def test_logs_to_player_when_given(monkeypatch):
    _config_state(monkeypatch)
    logged = []
    player = type("P", (), {"write_log": lambda self, msg: logged.append(msg)})()

    settings.configure_auto_response({"auto_reply_enabled": True}, player=player)

    assert logged
