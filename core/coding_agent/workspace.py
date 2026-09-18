"""
core/coding_agent/workspace.py — the sandbox boundary for the general-purpose
coding agent.

core/self_improvement/safety_guard.py is a DENYLIST protecting a handful of
paths inside Mark LIV's own source tree. This is the opposite shape: an
ALLOWLIST of exactly one root, because here the agent is meant to touch
arbitrary user projects freely - the risk is not "don't edit your own
engine", it's "never leave the sandbox". The containment check itself
mirrors the pattern actions/file_controller.py already uses for its
_SAFE_ROOTS / _is_safe_path, just scoped to one configurable workspace
instead of the whole home directory.

Enforced here in code, not left to a prompt - a coding-agent tool call that
would resolve outside the workspace root raises WorkspaceViolation before
any file or subprocess touches disk.
"""
from __future__ import annotations

from pathlib import Path

from memory.config_manager import get_workspace_root as _get_configured_root

_DEFAULT_ROOT = Path.home() / "Desktop" / "JarvisWorkspace"


class WorkspaceViolation(Exception):
    """A path or command would resolve outside the coding agent's workspace."""


def get_workspace_root() -> Path:
    """The sandbox root, created on first use. Configurable via
    memory.config_manager.save_workspace_root(); defaults to
    ~/Desktop/JarvisWorkspace, next to dev_agent's ~/Desktop/JarvisProjects."""
    raw = _get_configured_root()
    root = Path(raw).expanduser() if raw else _DEFAULT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_project_dir(project_path: str | None, root: Path | None = None) -> Path:
    """Turns a user-given project hint into an absolute directory INSIDE the
    workspace root. Omitted -> the root itself (the agent can inspect what is
    already there and create subfolders as it plans). A bare name
    ('my_game') becomes root/my_game. An absolute path is only accepted if it
    already resolves inside root."""
    root = root or get_workspace_root()
    if not project_path:
        return root
    raw = project_path.strip().strip('"').strip("'")
    if not raw:
        return root
    candidate = Path(raw).expanduser()
    target = candidate if candidate.is_absolute() else (root / raw)
    return ensure_within(target, root)


def ensure_within(path: Path, root: Path | None = None) -> Path:
    """Raises WorkspaceViolation unless `path` resolves inside `root`.
    Returns the resolved path so callers can use it immediately - the check
    and the value they act on can never drift apart."""
    root = root or get_workspace_root()
    try:
        resolved = path.resolve()
    except Exception as e:
        raise WorkspaceViolation(f"Could not resolve path '{path}': {e}")

    resolved_root = root.resolve()
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise WorkspaceViolation(
            f"'{path}' is outside the coding agent's workspace ({resolved_root}) "
            f"- refusing to touch it."
        )
    return resolved


def is_within(path: Path, root: Path | None = None) -> bool:
    try:
        ensure_within(path, root)
        return True
    except WorkspaceViolation:
        return False
