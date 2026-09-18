"""
core/self_improvement/rollback_manager.py — git-based isolation and rollback.

Every improvement attempt gets its own branch and worktree, created from the
currently checked-out HEAD. Whatever branch the user has checked out (main,
or anything else) is never edited directly - all writes happen inside the
worktree, which is a separate directory with its own working copy.

    success           -> promote(): worktree removed, branch kept for review.
    failure/rollback  -> discard(): worktree AND branch removed; nothing of
                         the attempt remains, and the user's checked-out
                         branch was never touched in the first place.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from memory.config_manager import get_base_dir

REPO_ROOT = get_base_dir()
WORKTREE_PARENT = REPO_ROOT / ".self_improvement_worktrees"


class GitError(Exception):
    pass


def _run(args: list[str], cwd: Path, timeout: int = 60) -> str:
    result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@dataclass
class Workspace:
    branch: str
    path: Path


def create_workspace(slug: str) -> Workspace:
    """Creates a new branch + worktree off the current HEAD.

    Refuses to start if the repo has uncommitted changes: a rollback removes
    the worktree, never the main checkout, so this isn't strictly needed for
    safety - but starting an attempt against a dirty tree makes 'what changed
    because of this improvement' impossible to read from the diff later, so
    it is refused rather than silently allowed."""
    status = _run(["status", "--porcelain"], cwd=REPO_ROOT)
    if status:
        raise GitError(
            "Working tree has uncommitted changes - commit or stash them first, "
            "so the improvement's diff is not mixed up with your own unsaved edits."
        )

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    branch = f"self-improvement/{timestamp}-{slug}"[:100]
    WORKTREE_PARENT.mkdir(parents=True, exist_ok=True)
    worktree_path = WORKTREE_PARENT / branch.replace("/", "__")

    _run(["worktree", "add", "-b", branch, str(worktree_path), "HEAD"], cwd=REPO_ROOT)
    return Workspace(branch=branch, path=worktree_path)


def diff(workspace: Workspace) -> str:
    return _run(["diff", "HEAD"], cwd=workspace.path)


def show_last_commit(workspace: Workspace) -> str:
    """The diff of the workspace's own most recent commit - used after
    commit() has already run, when `diff(HEAD)` would show nothing."""
    try:
        return _run(["show", "HEAD", "--stat", "-p"], cwd=workspace.path)
    except GitError:
        return ""


def commit(workspace: Workspace, message: str) -> None:
    _run(["add", "-A"], cwd=workspace.path)
    _run(["commit", "-m", message], cwd=workspace.path)


def _remove_worktree(workspace: Workspace) -> None:
    try:
        _run(["worktree", "remove", "--force", str(workspace.path)], cwd=REPO_ROOT)
    except GitError as e:
        print(f"[SelfImprovement] worktree remove failed, cleaning up manually: {e}")
        shutil.rmtree(workspace.path, ignore_errors=True)
        try:
            _run(["worktree", "prune"], cwd=REPO_ROOT)
        except GitError:
            pass


def discard(workspace: Workspace) -> None:
    """Full rollback: removes the worktree and deletes its branch. The
    branch the user had checked out was never touched, since it was never
    edited in the first place."""
    _remove_worktree(workspace)
    try:
        _run(["branch", "-D", workspace.branch], cwd=REPO_ROOT)
    except GitError:
        pass  # nothing was ever committed on it - nothing to delete


def promote(workspace: Workspace) -> None:
    """Leaves the branch (and its commit) in place for human review. Does
    NOT merge into any other branch and does NOT push anywhere - promotion
    here means 'the attempt is ready to be looked at', not 'it is live'."""
    _remove_worktree(workspace)


def reopen(branch: str) -> Workspace:
    """Recreates a worktree for an existing branch left behind by an
    'awaiting_approval' attempt, so approve()/reject() can act on it even if
    the process restarted in between."""
    worktree_path = WORKTREE_PARENT / branch.replace("/", "__")
    if not worktree_path.exists():
        WORKTREE_PARENT.mkdir(parents=True, exist_ok=True)
        _run(["worktree", "add", str(worktree_path), branch], cwd=REPO_ROOT)
    return Workspace(branch=branch, path=worktree_path)
