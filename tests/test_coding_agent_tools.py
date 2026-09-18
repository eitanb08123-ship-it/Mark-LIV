import pytest

from core import undo
from core.coding_agent import tools, workspace


@pytest.fixture(autouse=True)
def _clear_undo():
    undo.clear()
    yield
    undo.clear()


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "_get_configured_root", lambda: str(tmp_path))
    return tmp_path


def test_write_then_read_file(project_dir):
    result = tools.write_file(project_dir, "hello.py", "print('hi')")
    assert result.ok
    read = tools.read_file(project_dir, "hello.py")
    assert read.ok
    assert read.output == "print('hi')"


def test_read_missing_file_fails_cleanly(project_dir):
    result = tools.read_file(project_dir, "nope.py")
    assert not result.ok


def test_write_file_refuses_overwrite_when_disallowed(project_dir):
    tools.write_file(project_dir, "a.txt", "one")
    result = tools.write_file(project_dir, "a.txt", "two", overwrite=False)
    assert not result.ok
    assert tools.read_file(project_dir, "a.txt").output == "one"


def test_write_file_registers_undo(project_dir):
    tools.write_file(project_dir, "a.txt", "one")
    assert undo.can_undo()
    undo.undo_last()
    assert not (project_dir / "a.txt").exists()


def test_edit_file_requires_existing_file(project_dir):
    result = tools.edit_file(project_dir, "missing.py", "x = 1")
    assert not result.ok


def test_edit_file_updates_and_is_undoable(project_dir):
    tools.write_file(project_dir, "a.py", "x = 1")
    result = tools.edit_file(project_dir, "a.py", "x = 2")
    assert result.ok
    assert tools.read_file(project_dir, "a.py").output == "x = 2"
    undo.undo_last()
    assert tools.read_file(project_dir, "a.py").output == "x = 1"


def test_create_directory(project_dir):
    result = tools.create_directory(project_dir, "sub/dir")
    assert result.ok
    assert (project_dir / "sub" / "dir").is_dir()


def test_delete_file_moves_to_trash_and_is_undoable(project_dir):
    tools.write_file(project_dir, "a.txt", "content")
    result = tools.delete_file(project_dir, "a.txt")
    assert result.ok
    assert not (project_dir / "a.txt").exists()
    trashed = list((project_dir / ".jarvis_trash").iterdir())
    assert len(trashed) == 1
    undo.undo_last()
    assert (project_dir / "a.txt").read_text() == "content"


def test_delete_missing_file_fails_cleanly(project_dir):
    result = tools.delete_file(project_dir, "nope.txt")
    assert not result.ok


def test_list_directory(project_dir):
    tools.write_file(project_dir, "a.txt", "1")
    tools.create_directory(project_dir, "sub")
    result = tools.list_directory(project_dir)
    assert result.ok
    assert "a.txt" in result.output
    assert "sub" in result.output


def test_get_project_structure(project_dir):
    tools.write_file(project_dir, "pkg/mod.py", "x = 1")
    result = tools.get_project_structure(project_dir)
    assert result.ok
    assert "pkg" in result.output
    assert "mod.py" in result.output


def test_search_code_finds_match(project_dir):
    tools.write_file(project_dir, "a.py", "def foo():\n    return 42\n")
    result = tools.search_code(project_dir, "def foo")
    assert result.ok
    assert "a.py:1" in result.output


def test_search_code_no_match(project_dir):
    tools.write_file(project_dir, "a.py", "x = 1\n")
    result = tools.search_code(project_dir, "nonexistent_token")
    assert result.ok
    assert "No matches" in result.output


def test_run_command_executes_inside_workspace(project_dir):
    tools.write_file(project_dir, "hello.py", "print('hi there')")
    result = tools.run_command(project_dir, "python hello.py")
    assert result.ok
    assert "hi there" in result.output


def test_run_command_reports_failure(project_dir):
    tools.write_file(project_dir, "bad.py", "raise ValueError('boom')")
    result = tools.run_command(project_dir, "python bad.py")
    assert not result.ok
    assert "ValueError" in result.output


def test_run_tests_no_tests_collected_is_not_a_pass(project_dir):
    result = tools.run_tests(project_dir)
    assert not result.ok


def test_run_tests_passes_for_passing_test(project_dir):
    tools.create_directory(project_dir, "tests")
    tools.write_file(project_dir, "tests/test_x.py", "def test_ok():\n    assert 1 == 1\n")
    result = tools.run_tests(project_dir)
    assert result.ok


def test_path_outside_workspace_is_refused(project_dir, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "evil.py"
    result = tools.write_file(project_dir, str(outside), "x = 1")
    assert not result.ok
    assert "outside" in result.output.lower()
