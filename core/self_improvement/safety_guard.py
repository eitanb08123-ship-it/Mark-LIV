"""
core/self_improvement/safety_guard.py — a hard denylist the engine cannot
edit its way around.

WHY THIS IS CODE AND NOT A PROMPT INSTRUCTION
    Every other rule the improvement loop follows (scope, risk, style) comes
    from a prompt fed to a model, and a model can be argued out of a prompt.
    A safety boundary that only exists as words in a prompt is not a boundary;
    it is a suggestion. So the paths and keywords below are checked here, in
    plain Python, before a single byte is written - not asked of Gemini.

WHAT CANNOT BE TOUCHED, EVER, REGARDLESS OF AUTO_IMPROVEMENT
    - This package (core/self_improvement/): the engine cannot rewrite its
      own safety machinery, evaluator, or history.
    - config/api_keys.json: credentials.
    - memory/self_improvement_history.json: its own audit trail - an engine
      that can erase its own history could hide a bad attempt from the human
      reviewing it.
    - core/confirm.py: the approval gate itself.

Extending this list requires editing this file by hand, which is the point:
no code path in the engine ever adds to _FORBIDDEN_PATHS or
_FORBIDDEN_KEYWORDS at runtime.
"""
from __future__ import annotations

from pathlib import Path

from memory.config_manager import get_base_dir

BASE_DIR = get_base_dir()

_FORBIDDEN_PATHS = [
    BASE_DIR / "core" / "self_improvement",
    BASE_DIR / "config" / "api_keys.json",
    BASE_DIR / "memory" / "self_improvement_history.json",
    BASE_DIR / "core" / "confirm.py",
]

# If a proposed change to a file OUTSIDE this package's own source starts
# referencing these names, that is worth a human's eyes before it goes
# further - it is exactly the shape of an attempt to weaken the guard rails
# from the outside (e.g. editing main.py to stop calling safety_guard).
_FORBIDDEN_KEYWORDS = (
    "api_key", "auth_token", "password", "secret", "credential",
    "auto_improvement", "safety_guard", "safetyviolation",
)


class SafetyViolation(Exception):
    """Raised when a proposed change touches something on the denylist.
    The engine catches this and records the attempt as blocked - it is
    never allowed to propagate into a silent failure."""


def _resolve(path) -> Path:
    return Path(path).resolve()


def check_path(path) -> None:
    """Raises SafetyViolation if `path` is, or is inside, a forbidden path.
    `path` may be absolute or relative to BASE_DIR."""
    candidate = Path(path)
    resolved = _resolve(candidate if candidate.is_absolute() else BASE_DIR / candidate)
    for forbidden in _FORBIDDEN_PATHS:
        forbidden_resolved = _resolve(forbidden)
        if resolved == forbidden_resolved or forbidden_resolved in resolved.parents:
            raise SafetyViolation(f"'{path}' is on the self-improvement denylist ({forbidden}).")


def check_content(path, new_content: str) -> None:
    """Raises SafetyViolation if `path` itself is forbidden (see check_path),
    or if the content references something that looks like an attempt to
    touch credentials or the safety machinery itself. There is no exemption
    for files inside this package - check_path already denies writing to
    any of them, so this always applies in full to whatever is left."""
    check_path(path)
    lowered = new_content.lower()
    for keyword in _FORBIDDEN_KEYWORDS:
        if keyword in lowered:
            raise SafetyViolation(
                f"Proposed change to '{path}' references '{keyword}', which requires manual review "
                "before it can be applied by the self-improvement engine."
            )
