"""
actions/auto_reply_settings.py tests — the tool JARVIS calls from a normal
conversation to turn auto-reply / call auto-answer on or off. Pins: each
flag is only touched when its parameter is actually passed, invalid
platform names are dropped rather than accepted verbatim, an empty call
just reports status without changing anything, and the reported status
always reflects config_manager's real (possibly monkeypatched) state.
"""
import pytest

from actions import auto_reply_settings as settings


def _config_state(monkeypatch, **overrides):
    state = {
        "auto_reply_enabled": False,
        "auto_reply_platforms": ["instagram"],
        "whatsapp_call_answer_enabled": False,
        "instagram_auto_answer_enabled": False,
        "auto_reply_dry_run": False,
        "auto_reply_contacts": [],
    }
    state.update(overrides)

    monkeypatch.setattr(settings, "get_auto_reply_enabled", lambda: state["auto_reply_enabled"])
    monkeypatch.setattr(settings, "get_auto_reply_platforms", lambda: state["auto_reply_platforms"])
    monkeypatch.setattr(settings, "get_whatsapp_call_answer_enabled", lambda: state["whatsapp_call_answer_enabled"])
    monkeypatch.setattr(settings, "get_instagram_auto_answer_enabled", lambda: state["instagram_auto_answer_enabled"])
    monkeypatch.setattr(settings, "get_auto_reply_dry_run", lambda: state["auto_reply_dry_run"])
    monkeypatch.setattr(settings, "get_auto_reply_contacts", lambda: state["auto_reply_contacts"])

    monkeypatch.setattr(settings, "save_auto_reply_enabled",
                         lambda v: state.__setitem__("auto_reply_enabled", v))
    monkeypatch.setattr(settings, "save_auto_reply_platforms",
                         lambda v: state.__setitem__("auto_reply_platforms", v))
    monkeypatch.setattr(settings, "save_whatsapp_call_answer_enabled",
                         lambda v: state.__setitem__("whatsapp_call_answer_enabled", v))
    monkeypatch.setattr(settings, "save_instagram_auto_answer_enabled",
                         lambda v: state.__setitem__("instagram_auto_answer_enabled", v))
    monkeypatch.setattr(settings, "save_auto_reply_dry_run",
                         lambda v: state.__setitem__("auto_reply_dry_run", v))
    monkeypatch.setattr(settings, "save_auto_reply_contacts",
                         lambda v: state.__setitem__("auto_reply_contacts", v))
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
        "auto_reply_dry_run": False,
        "auto_reply_contacts": [],
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


# ── focus/maximize on activation ────────────────────────────────────────────

def test_activating_auto_reply_focuses_and_maximizes_each_desktop_platform(monkeypatch):
    state = _config_state(monkeypatch)
    calls = []
    monkeypatch.setattr(settings, "_focus_and_maximize",
                        lambda app_name: calls.append(app_name) or f"{app_name} window focused and maximized.")

    result = settings.configure_auto_response({
        "auto_reply_enabled": True,
        "auto_reply_platforms": ["whatsapp", "telegram", "instagram"],
    })

    assert calls == ["Whatsapp", "Telegram"]   # instagram is browser-based, not a desktop window
    assert "focused and maximized" in result.lower()


def test_activating_auto_reply_with_already_configured_platforms_still_focuses(monkeypatch):
    """Focus must fire using the FINAL platform list even when platforms
    weren't part of THIS call - e.g. auto_reply_platforms was set to
    ["whatsapp"] earlier, and this call only flips auto_reply_enabled."""
    state = _config_state(monkeypatch, auto_reply_platforms=["whatsapp"])
    calls = []
    monkeypatch.setattr(settings, "_focus_and_maximize",
                        lambda app_name: calls.append(app_name) or "ok")

    settings.configure_auto_response({"auto_reply_enabled": True})

    assert calls == ["Whatsapp"]


def test_disabling_auto_reply_does_not_focus_anything(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_enabled=True, auto_reply_platforms=["whatsapp"])
    calls = []
    monkeypatch.setattr(settings, "_focus_and_maximize",
                        lambda app_name: calls.append(app_name) or "ok")

    settings.configure_auto_response({"auto_reply_enabled": False})

    assert calls == []


def test_status_only_call_does_not_focus_anything(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_enabled=True, auto_reply_platforms=["whatsapp"])
    calls = []
    monkeypatch.setattr(settings, "_focus_and_maximize",
                        lambda app_name: calls.append(app_name) or "ok")

    settings.configure_auto_response({})

    assert calls == []


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


# ── Strict boolean validation (item 10) ─────────────────────────────────────
# bool("false") is True in Python - a malformed payload sending the STRING
# "false" used to silently ENABLE a feature meant to stay off. Every
# boolean-typed field must now be a real Python bool or the whole call is
# rejected before anything is written.

@pytest.mark.parametrize("bad_value", ["true", "false", 0, 1, None, [], {}, ["true"]])
def test_non_bool_auto_reply_enabled_is_rejected_without_writing(monkeypatch, bad_value):
    state = _config_state(monkeypatch)

    result = settings.configure_auto_response({"auto_reply_enabled": bad_value})

    assert "invalid" in result.lower()
    assert state["auto_reply_enabled"] is False   # unchanged


@pytest.mark.parametrize("field", [
    "auto_reply_enabled", "whatsapp_call_answer_enabled", "instagram_call_answer_enabled",
    "auto_reply_dry_run",
])
@pytest.mark.parametrize("bad_value", ["true", "false", 0, 1, None])
def test_every_bool_field_rejects_non_bool_values(monkeypatch, field, bad_value):
    state = _config_state(monkeypatch)
    snapshot = dict(state)

    result = settings.configure_auto_response({field: bad_value})

    assert "invalid" in result.lower()
    assert state == snapshot   # nothing was written


def test_real_true_and_false_are_accepted(monkeypatch):
    state = _config_state(monkeypatch)

    settings.configure_auto_response({"auto_reply_enabled": True})
    assert state["auto_reply_enabled"] is True

    settings.configure_auto_response({"auto_reply_enabled": False})
    assert state["auto_reply_enabled"] is False


def test_invalid_platforms_type_is_rejected_without_writing(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_platforms=["instagram"])

    result = settings.configure_auto_response({"auto_reply_platforms": "whatsapp"})

    assert "invalid" in result.lower()
    assert state["auto_reply_platforms"] == ["instagram"]


def test_platforms_list_with_a_non_string_entry_is_rejected(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_platforms=["instagram"])

    result = settings.configure_auto_response({"auto_reply_platforms": ["whatsapp", 123]})

    assert "invalid" in result.lower()
    assert state["auto_reply_platforms"] == ["instagram"]


def test_one_invalid_field_blocks_the_whole_call_atomically(monkeypatch):
    """Acceptance criterion: no partial apply. A valid field alongside an
    invalid one must change NOTHING, not just skip the bad one."""
    state = _config_state(monkeypatch)

    result = settings.configure_auto_response({
        "auto_reply_enabled": True,           # valid
        "whatsapp_call_answer_enabled": "false",  # invalid
    })

    assert "invalid" in result.lower()
    assert state["auto_reply_enabled"] is False
    assert state["whatsapp_call_answer_enabled"] is False


# ── dry-run / shadow mode toggle ─────────────────────────────────────────────

def test_enables_dry_run_mode(monkeypatch):
    state = _config_state(monkeypatch)

    result = settings.configure_auto_response({"auto_reply_dry_run": True})

    assert state["auto_reply_dry_run"] is True
    assert "dry-run mode turned on" in result.lower()


def test_status_reports_dry_run_state(monkeypatch):
    _config_state(monkeypatch, auto_reply_dry_run=True)

    result = settings.configure_auto_response({})

    assert "dry-run mode: on" in result.lower()


def test_enabling_dry_run_alone_does_not_focus_anything(monkeypatch):
    """Dry-run never touches the desktop - it must not trigger the
    focus/maximize-on-activation behavior that a real auto_reply_enabled
    toggle does."""
    state = _config_state(monkeypatch)
    calls = []
    monkeypatch.setattr(settings, "_focus_and_maximize",
                        lambda app_name: calls.append(app_name) or "ok")

    settings.configure_auto_response({"auto_reply_dry_run": True})

    assert calls == []


# ── per-contact allow-list ───────────────────────────────────────────────────

def test_sets_the_contact_allow_list(monkeypatch):
    state = _config_state(monkeypatch)

    result = settings.configure_auto_response({"auto_reply_contacts": ["Mom"]})

    assert state["auto_reply_contacts"] == ["Mom"]
    assert "allow-list set to mom" in result.lower()


def test_clearing_the_contact_allow_list_is_reported_distinctly(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_contacts=["Mom"])

    result = settings.configure_auto_response({"auto_reply_contacts": []})

    assert state["auto_reply_contacts"] == []
    assert "cleared" in result.lower()
    assert "all contacts" in result.lower()


def test_contact_allow_list_strips_blank_entries(monkeypatch):
    state = _config_state(monkeypatch)

    settings.configure_auto_response({"auto_reply_contacts": ["Mom", "  ", ""]})

    assert state["auto_reply_contacts"] == ["Mom"]


def test_status_reports_the_allow_list(monkeypatch):
    _config_state(monkeypatch, auto_reply_enabled=True, auto_reply_platforms=["whatsapp"],
                   auto_reply_contacts=["Mom"])

    result = settings.configure_auto_response({})

    assert "contacts: mom" in result.lower()


def test_status_reports_all_when_no_allow_list_is_set(monkeypatch):
    _config_state(monkeypatch, auto_reply_enabled=True, auto_reply_platforms=["whatsapp"])

    result = settings.configure_auto_response({})

    assert "contacts: all" in result.lower()


def test_invalid_contacts_type_is_rejected_without_writing(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_contacts=["Mom"])

    result = settings.configure_auto_response({"auto_reply_contacts": "Mom"})

    assert "invalid" in result.lower()
    assert state["auto_reply_contacts"] == ["Mom"]


def test_contacts_list_with_a_non_string_entry_is_rejected(monkeypatch):
    state = _config_state(monkeypatch, auto_reply_contacts=["Mom"])

    result = settings.configure_auto_response({"auto_reply_contacts": ["Dana", 123]})

    assert "invalid" in result.lower()
    assert state["auto_reply_contacts"] == ["Mom"]


def test_setting_contacts_alone_does_not_focus_anything(monkeypatch):
    state = _config_state(monkeypatch)
    calls = []
    monkeypatch.setattr(settings, "_focus_and_maximize",
                        lambda app_name: calls.append(app_name) or "ok")

    settings.configure_auto_response({"auto_reply_contacts": ["Mom"]})

    assert calls == []
