import pytest

from core.self_improvement import safety_guard


def test_denies_its_own_package():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_path("core/self_improvement/engine.py")


def test_denies_credentials_file():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_path("config/api_keys.json")


def test_denies_own_history_file():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_path("memory/self_improvement_history.json")


def test_denies_confirm_gate():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_path("core/confirm.py")


def test_allows_ordinary_action_file():
    safety_guard.check_path("actions/open_app.py")  # must not raise


def test_denies_path_traversal_into_forbidden_dir():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_path("actions/../core/self_improvement/engine.py")


def test_check_content_denies_credential_keyword_outside_package():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_content("actions/some_action.py", "API_KEY = 'sk-abc123'")


def test_check_content_allows_ordinary_code():
    safety_guard.check_content("actions/some_action.py", "def run():\n    return 'ok'\n")  # must not raise


def test_check_content_still_denies_own_package_regardless_of_content():
    # check_path already forbids the whole package - check_content must not
    # carve out an exception for it based on what the content says.
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_content("core/self_improvement/engine.py", "print('hello')")


# ── auto-reply's own safety nets are the OTHER dangerous feature in this ────
# codebase (sends AI-authored messages under the user's identity with no
# human review) - the engine must never be able to weaken them either.

def test_denies_auto_reply_module_itself():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_path("actions/auto_reply.py")


def test_denies_conversation_history_module_itself():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_path("memory/conversation_history.py")


def test_check_content_denies_a_new_file_that_reimplements_auto_reply_logic():
    """The exact scenario the keyword class exists for: a NEW file (not
    itself on _FORBIDDEN_PATHS) that reimplements or calls into the
    safety-critical primitives elsewhere."""
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_content(
            "actions/auto_reply_v2.py",
            "def replied_too_recently(platform, contact):\n    return False\n",
        )


def test_check_content_denies_editing_main_to_reference_auto_reply():
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check_content("main.py", "from actions.auto_reply import auto_reply_cycle")
