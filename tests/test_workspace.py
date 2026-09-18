import pytest

from core.coding_agent import workspace


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setattr(workspace, "_get_configured_root", lambda: str(root))
    return root


def test_get_workspace_root_creates_it(fake_root):
    root = workspace.get_workspace_root()
    assert root == fake_root.resolve()
    assert root.exists()


def test_resolve_project_dir_defaults_to_root_when_omitted(fake_root):
    root = workspace.get_workspace_root()
    assert workspace.resolve_project_dir(None, root) == root
    assert workspace.resolve_project_dir("", root) == root


def test_resolve_project_dir_relative_name(fake_root):
    root = workspace.get_workspace_root()
    result = workspace.resolve_project_dir("my_game", root)
    assert result == (root / "my_game").resolve()


def test_resolve_project_dir_denies_absolute_path_outside_root(fake_root, tmp_path):
    root = workspace.get_workspace_root()
    outside = tmp_path / "elsewhere"
    with pytest.raises(workspace.WorkspaceViolation):
        workspace.resolve_project_dir(str(outside), root)


def test_ensure_within_allows_path_inside_root(fake_root):
    root = workspace.get_workspace_root()
    target = root / "sub" / "file.py"
    assert workspace.ensure_within(target, root) == target.resolve()


def test_ensure_within_denies_path_outside_root(fake_root, tmp_path):
    root = workspace.get_workspace_root()
    outside = tmp_path / "elsewhere" / "file.py"
    with pytest.raises(workspace.WorkspaceViolation):
        workspace.ensure_within(outside, root)


def test_ensure_within_denies_path_traversal_out_of_root(fake_root):
    root = workspace.get_workspace_root()
    escaping = root / ".." / "escaped.py"
    with pytest.raises(workspace.WorkspaceViolation):
        workspace.ensure_within(escaping, root)


def test_is_within(fake_root, tmp_path):
    root = workspace.get_workspace_root()
    assert workspace.is_within(root / "a.py", root) is True
    assert workspace.is_within(tmp_path / "outside" / "a.py", root) is False
