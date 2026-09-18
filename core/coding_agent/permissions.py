"""
core/coding_agent/permissions.py — read/write/execute/delete/install gating
for the general-purpose coding agent, enforced here in code (never left to a
prompt, matching core/self_improvement/safety_guard.py's philosophy).

  READ    (read_file, list_directory, search_code, get_project_structure)
          - always allowed. Nothing reversible-or-not is at stake.
  WRITE   (write_file, edit_file, create_directory)
          - allowed automatically: this is the agent's whole job, and every
            write registers an undo (core/undo.py) so a bad one is one
            command away from reversed - the exact tradeoff core/confirm.py
            itself documents ("reversible -> undo, not a question").
  run_tests
          - always allowed: read-only from the workspace's point of view,
            and the "test, read errors, fix, test again" loop depends on it
            not needing a human every iteration.
  EXECUTE (run_command)
          - needs on-screen confirmation UNLESS
            memory.config_manager.get_coding_agent_auto_execute() is on. A
            hard-denylist of especially destructive patterns (sudo, rm -rf /,
            disk/format tools, fork bombs, force-pushes, ...) is refused
            OUTRIGHT and cannot be auto-approved at all, mirroring
            safety_guard's unconditional denials.
  INSTALL (a run_command recognised as a package-manager install)
          - same idea as EXECUTE, but gated by its own
            coding_agent_auto_install flag, so "let it run build/test
            commands on its own" and "let it install packages on its own"
            can be turned on independently.
  DELETE  (delete_file)
          - ALWAYS needs confirmation, regardless of any auto-* flag. Files
            land in a recoverable .jarvis_trash/ (see tools.py) but the gate
            itself never relaxes - deleting the wrong file is the single
            most common "it did the wrong thing" a coding agent can cause.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

from memory.config_manager import get_coding_agent_auto_execute, get_coding_agent_auto_install

_READ_TOOLS = {"read_file", "list_directory", "search_code", "get_project_structure"}
_WRITE_TOOLS = {"write_file", "edit_file", "create_directory"}

# Never runs, confirmation or no - defense in depth on top of the workspace
# sandbox, since a command can still name an absolute path outside it.
_HARD_DENY_PATTERNS = [
    r"\bsudo\b",
    r"\brm\s+-rf\s+/(?:\s|$)",
    r"\brm\s+-rf\s+~",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bgit\s+push\b.*--force",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",   # classic fork bomb
    r"\bformat\s+[a-zA-Z]:",
    r"\bdel\s+/f\s+/s\s+/q\b",
]
_INSTALL_PATTERNS = [
    r"\bpip3?\s+install\b",
    r"\bnpm\s+(install|i)\b",
    r"\byarn\s+add\b",
    r"\bapt(-get)?\s+install\b",
    r"\bbrew\s+install\b",
]


@dataclass
class Gate:
    """A confirmation banner must be shown before this call may proceed."""
    key: str
    title: str
    detail: str


@dataclass
class Denied:
    """This call must never run, confirmed or not."""
    reason: str


def check(tool: str, args: dict, project_dir) -> Optional[Union[Gate, Denied]]:
    """None -> proceed immediately. Denied -> never run it. Gate -> show a
    confirmation banner first (core/confirm.py) and only run it if accepted."""
    args = args or {}

    if tool in _READ_TOOLS or tool == "run_tests":
        return None

    if tool in _WRITE_TOOLS:
        return None

    if tool == "delete_file":
        path = args.get("path", "")
        return Gate(
            key=f"coding_agent_delete_{path}",
            title=f"Delete '{path}'?",
            detail=(f"The coding agent wants to delete '{path}' inside {project_dir}. "
                    f"It will be moved to .jarvis_trash/ and can be restored afterwards."),
        )

    if tool == "run_command":
        command = str(args.get("command", ""))
        for pat in _HARD_DENY_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return Denied(f"Refusing to run '{command}' - matches a hard-denied pattern.")

        is_install = any(re.search(p, command, re.IGNORECASE) for p in _INSTALL_PATTERNS)
        if is_install:
            if get_coding_agent_auto_install():
                return None
            return Gate(
                key=f"coding_agent_install_{command}",
                title="Install dependency?",
                detail=f"The coding agent wants to run:\n{command}\nin {project_dir}.",
            )

        if get_coding_agent_auto_execute():
            return None
        return Gate(
            key=f"coding_agent_run_{command}",
            title="Run command?",
            detail=f"The coding agent wants to run:\n{command}\nin {project_dir}.",
        )

    return Denied(f"Unknown tool '{tool}'.")
