import pytest

from core.coding_agent import permissions


def test_read_tools_always_allowed():
    for tool in ("read_file", "list_directory", "search_code", "get_project_structure"):
        assert permissions.check(tool, {"path": "a.py"}, "/tmp/project") is None


def test_run_tests_always_allowed():
    assert permissions.check("run_tests", {}, "/tmp/project") is None


def test_write_tools_always_allowed():
    for tool in ("write_file", "edit_file", "create_directory"):
        assert permissions.check(tool, {"path": "a.py"}, "/tmp/project") is None


def test_delete_is_always_gated_even_with_every_auto_flag_on(monkeypatch):
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: True)
    monkeypatch.setattr(permissions, "get_coding_agent_auto_install", lambda: True)
    gate = permissions.check("delete_file", {"path": "a.py"}, "/tmp/project")
    assert isinstance(gate, permissions.Gate)


def test_run_command_gated_by_default(monkeypatch):
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: False)
    gate = permissions.check("run_command", {"command": "python main.py"}, "/tmp/project")
    assert isinstance(gate, permissions.Gate)


def test_run_command_auto_execute_allows_ordinary_command(monkeypatch):
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: True)
    result = permissions.check("run_command", {"command": "python main.py"}, "/tmp/project")
    assert result is None


def test_install_command_gated_independently_of_execute(monkeypatch):
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: True)
    monkeypatch.setattr(permissions, "get_coding_agent_auto_install", lambda: False)
    result = permissions.check("run_command", {"command": "pip install requests"}, "/tmp/project")
    assert isinstance(result, permissions.Gate)


def test_install_command_auto_install_allows_it(monkeypatch):
    monkeypatch.setattr(permissions, "get_coding_agent_auto_install", lambda: True)
    result = permissions.check("run_command", {"command": "pip install requests"}, "/tmp/project")
    assert result is None


@pytest.mark.parametrize("command", [
    "sudo rm -rf /",
    "rm -rf /",
    "rm -rf ~",
    "mkfs.ext4 /dev/sda1",
    "shutdown now",
    "git push origin main --force",
])
def test_hard_denied_commands_are_never_allowed(monkeypatch, command):
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: True)
    monkeypatch.setattr(permissions, "get_coding_agent_auto_install", lambda: True)
    result = permissions.check("run_command", {"command": command}, "/tmp/project")
    assert isinstance(result, permissions.Denied)


def test_ordinary_command_is_not_hard_denied(monkeypatch):
    monkeypatch.setattr(permissions, "get_coding_agent_auto_execute", lambda: True)
    result = permissions.check("run_command", {"command": "python main.py"}, "/tmp/project")
    assert result is None


def test_unknown_tool_is_denied():
    result = permissions.check("delete_everything", {}, "/tmp/project")
    assert isinstance(result, permissions.Denied)
