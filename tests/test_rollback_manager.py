import subprocess

import pytest

from core.self_improvement import rollback_manager


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """A real, throwaway git repo - never the actual Mark LIV checkout - with
    one committed file, so create_workspace() has a clean HEAD to branch from."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

    monkeypatch.setattr(rollback_manager, "REPO_ROOT", repo)
    monkeypatch.setattr(rollback_manager, "WORKTREE_PARENT", repo / ".self_improvement_worktrees")
    return repo


def test_create_workspace_branches_off_head(temp_repo):
    workspace = rollback_manager.create_workspace("test-slug")

    assert workspace.path.exists()
    assert (workspace.path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert "self-improvement/" in workspace.branch
    assert "test-slug" in workspace.branch

    rollback_manager.discard(workspace)


def test_create_workspace_refuses_dirty_tree(temp_repo):
    (temp_repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")  # uncommitted change

    with pytest.raises(rollback_manager.GitError):
        rollback_manager.create_workspace("should-fail")


def test_edit_commit_and_diff(temp_repo):
    workspace = rollback_manager.create_workspace("edit-test")
    (workspace.path / "app.py").write_text("VALUE = 99\n", encoding="utf-8")

    diff_text = rollback_manager.diff(workspace)
    assert "VALUE = 99" in diff_text

    rollback_manager.commit(workspace, "bump value")
    # after commit, `diff HEAD` in the worktree is empty; show_last_commit sees it
    assert rollback_manager.diff(workspace) == ""
    assert "VALUE = 99" in rollback_manager.show_last_commit(workspace)

    rollback_manager.discard(workspace)


def test_discard_removes_worktree_and_branch_and_leaves_main_untouched(temp_repo):
    workspace = rollback_manager.create_workspace("to-discard")
    (workspace.path / "app.py").write_text("VALUE = 999\n", encoding="utf-8")
    rollback_manager.commit(workspace, "bad change")

    rollback_manager.discard(workspace)

    assert not workspace.path.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", workspace.branch], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert workspace.branch not in branches
    # main's own checked-out file was never touched by any of this.
    assert (temp_repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_promote_keeps_branch_removes_worktree(temp_repo):
    workspace = rollback_manager.create_workspace("to-promote")
    (workspace.path / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
    rollback_manager.commit(workspace, "good change")

    rollback_manager.promote(workspace)

    assert not workspace.path.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", workspace.branch], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert workspace.branch in branches
    # main's own checked-out file is still untouched - the change lives only on the branch.
    assert (temp_repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
