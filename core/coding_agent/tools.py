"""
core/coding_agent/tools.py — the atomic, workspace-sandboxed tool set the
coding agent's loop (core/coding_agent/agent_loop.py) composes into
multi-step tasks: read_file, write_file, edit_file, list_directory,
search_code, create_directory, delete_file, run_command, run_tests,
get_project_structure.

Every tool resolves its path through core/coding_agent/workspace.py before
touching disk or spawning a process - the same "enforced in code, never left
to a prompt" rule core/self_improvement/safety_guard.py already applies to
Mark LIV's own source tree.

Reversible operations (write/edit/create/delete) register themselves with
core/undo.py exactly the way actions/file_controller.py already does, so a
coding-agent mistake is one "undo" away instead of a manual cleanup -
matching core/confirm.py's own stated philosophy: reversible things act
immediately and get an undo, only genuinely irreversible things ask first.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from core.self_improvement import test_runner
from core.undo import push_undo

from . import workspace

_MAX_READ_BYTES = 200_000     # bigger than this is not something to paste whole into a prompt
_MAX_SEARCH_MATCHES = 200
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
             ".pytest_cache", ".mypy_cache", ".jarvis_trash"}


@dataclass
class ToolResult:
    ok: bool
    output: str


def _target(project_dir: Path, rel_path: str) -> Path:
    rel_path = (rel_path or "").strip().strip('"').strip("'")
    p = Path(rel_path) if rel_path else project_dir
    candidate = p if p.is_absolute() else (project_dir / p)
    return workspace.ensure_within(candidate)


def _undo_write(target: Path, previous: str | None):
    def _fn():
        if previous is None:
            if target.exists():
                target.unlink()
                return f"Removed '{target.name}' - it did not exist before."
            return f"'{target.name}' is already gone."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(previous, encoding="utf-8")
        return f"Restored the previous contents of '{target.name}'."
    return _fn


def read_file(project_dir: Path, path: str) -> ToolResult:
    try:
        target = _target(project_dir, path)
        if not target.exists():
            return ToolResult(False, f"'{path}' does not exist.")
        if not target.is_file():
            return ToolResult(False, f"'{path}' is not a file.")
        data = target.read_bytes()
        if len(data) > _MAX_READ_BYTES:
            return ToolResult(False, f"'{path}' is too large to read whole ({len(data)} bytes).")
        return ToolResult(True, data.decode("utf-8", errors="replace"))
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Could not read '{path}': {e}")


def write_file(project_dir: Path, path: str, content: str, overwrite: bool = True) -> ToolResult:
    try:
        target = _target(project_dir, path)
        existed = target.exists()
        if existed and not overwrite:
            return ToolResult(False, f"'{path}' already exists - use edit_file to change it.")
        previous = target.read_text(encoding="utf-8", errors="replace") if existed else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content is not None else "", encoding="utf-8")
        push_undo(f"wrote {target.name}", _undo_write(target, previous))
        return ToolResult(True, f"Wrote {len(content or '')} chars to '{path}'.")
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Could not write '{path}': {e}")


def edit_file(project_dir: Path, path: str, content: str) -> ToolResult:
    """Same mechanics as write_file, but the target must already exist -
    keeping 'create' vs 'modify' distinct for the agent's own reasoning.
    Rewrites the whole file rather than diffing/patching, matching the
    convention core/self_improvement/executor.py already established."""
    try:
        target = _target(project_dir, path)
        if not target.exists():
            return ToolResult(False, f"'{path}' does not exist yet - use write_file to create it.")
        previous = target.read_text(encoding="utf-8", errors="replace")
        target.write_text(content if content is not None else "", encoding="utf-8")
        push_undo(f"edited {target.name}", _undo_write(target, previous))
        return ToolResult(True, f"Updated '{path}' ({len(content or '')} chars).")
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Could not edit '{path}': {e}")


def create_directory(project_dir: Path, path: str) -> ToolResult:
    try:
        target = _target(project_dir, path)
        if target.exists():
            return ToolResult(True, f"'{path}' already exists.")
        target.mkdir(parents=True, exist_ok=True)

        def _undo():
            if target.exists() and not any(target.iterdir()):
                target.rmdir()
                return f"Removed '{target.name}'."
            return f"'{target.name}' is not empty - leaving it."
        push_undo(f"created directory {target.name}", _undo)
        return ToolResult(True, f"Created '{path}'.")
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Could not create '{path}': {e}")


def delete_file(project_dir: Path, path: str) -> ToolResult:
    """Soft-delete: moved into a per-project '.jarvis_trash/' folder inside
    the workspace instead of vanishing outright, on top of the confirmation
    core/coding_agent/permissions.py always requires before this even runs -
    a real removal from the project, but recoverable with one 'undo'."""
    try:
        target = _target(project_dir, path)
        if not target.exists():
            return ToolResult(False, f"'{path}' does not exist.")
        trash_dir = workspace.ensure_within(project_dir / ".jarvis_trash")
        trash_dir.mkdir(parents=True, exist_ok=True)
        dest = trash_dir / f"{target.name}.{int(time.time())}"
        target.rename(dest)

        def _undo():
            if not dest.exists():
                return f"'{target.name}' is no longer in the trash."
            target.parent.mkdir(parents=True, exist_ok=True)
            dest.rename(target)
            return f"Restored '{target.name}'."
        push_undo(f"deleted {target.name}", _undo)
        return ToolResult(True, f"Moved '{path}' to .jarvis_trash/ (recoverable via undo).")
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Could not delete '{path}': {e}")


def list_directory(project_dir: Path, path: str = "") -> ToolResult:
    try:
        target = _target(project_dir, path)
        if not target.exists():
            return ToolResult(False, f"'{path or '.'}' does not exist.")
        if not target.is_dir():
            return ToolResult(False, f"'{path}' is not a directory.")
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines = [f"{'file' if e.is_file() else 'dir '}  {e.name}"
                for e in entries if e.name not in _SKIP_DIRS]
        return ToolResult(True, "\n".join(lines) if lines else "(empty)")
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Could not list '{path}': {e}")


def get_project_structure(project_dir: Path, max_depth: int = 4, max_entries: int = 400) -> ToolResult:
    try:
        root = workspace.ensure_within(project_dir)
        if not root.exists():
            return ToolResult(True, "(project directory does not exist yet - it will be created)")
        lines: list[str] = []

        def _walk(dir_path: Path, prefix: str, depth: int) -> None:
            if depth > max_depth or len(lines) >= max_entries:
                return
            try:
                entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except Exception:
                return
            for e in entries:
                if e.name in _SKIP_DIRS or e.name.startswith("."):
                    continue
                if len(lines) >= max_entries:
                    lines.append(f"{prefix}... (truncated)")
                    return
                lines.append(f"{prefix}{e.name}{'/' if e.is_dir() else ''}")
                if e.is_dir():
                    _walk(e, prefix + "  ", depth + 1)

        _walk(root, "", 0)
        return ToolResult(True, "\n".join(lines) if lines else "(empty directory)")
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Could not read project structure: {e}")


def search_code(project_dir: Path, query: str, path: str = "", glob: str = "*") -> ToolResult:
    try:
        root = _target(project_dir, path)
        if not root.exists():
            return ToolResult(False, f"'{path or '.'}' does not exist.")
        query_l = (query or "").lower()
        if not query_l:
            return ToolResult(False, "search_code needs a non-empty query.")

        if root.is_file():
            files = [root]
        else:
            files = [
                f for f in root.rglob("*")
                if f.is_file() and not any(part in _SKIP_DIRS for part in f.parts)
                and fnmatch.fnmatch(f.name, glob or "*")
            ]

        matches: list[str] = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if query_l in line.lower():
                    try:
                        rel = f.relative_to(project_dir)
                    except ValueError:
                        rel = f
                    matches.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(matches) >= _MAX_SEARCH_MATCHES:
                        break
            if len(matches) >= _MAX_SEARCH_MATCHES:
                break

        if not matches:
            return ToolResult(True, f"No matches for '{query}'.")
        return ToolResult(True, "\n".join(matches))
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except Exception as e:
        return ToolResult(False, f"Search failed: {e}")


def run_command(project_dir: Path, command: str, timeout: int = 60) -> ToolResult:
    try:
        cwd = workspace.ensure_within(project_dir)
        cwd.mkdir(parents=True, exist_ok=True)
        command = (command or "").strip()
        if not command:
            return ToolResult(False, "run_command needs a non-empty command.")
        parts = command.split()
        # sys.executable, not a bare "python"/"python3": on some systems that
        # can resolve to a different interpreter than the one running this
        # process (and its installed packages) - see test_runner.py's own note.
        if parts and parts[0].lower() in ("python", "python3"):
            parts[0] = sys.executable
        result = subprocess.run(
            parts, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = ""
        if result.stdout.strip():
            out += f"STDOUT:\n{result.stdout.strip()[-4000:]}\n"
        if result.stderr.strip():
            out += f"STDERR:\n{result.stderr.strip()[-4000:]}\n"
        out += f"(exit code {result.returncode})"
        return ToolResult(result.returncode == 0, out)
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
    except subprocess.TimeoutExpired:
        return ToolResult(False, f"Command timed out after {timeout}s.")
    except FileNotFoundError as e:
        return ToolResult(False, f"Command not found: {e}")
    except Exception as e:
        return ToolResult(False, f"Command failed: {e}")


def run_tests(project_dir: Path, target: str | None = None, timeout: int = 120) -> ToolResult:
    try:
        cwd = workspace.ensure_within(project_dir)
        result = test_runner.run_tests(cwd, target=target, timeout=timeout)
        body = result.stdout
        if result.stderr:
            body = f"{body}\nSTDERR:\n{result.stderr}" if body else result.stderr
        if not result.ran:
            return ToolResult(False, body or "pytest could not be run.")
        return ToolResult(result.passed, body or "(no output)")
    except workspace.WorkspaceViolation as e:
        return ToolResult(False, str(e))
