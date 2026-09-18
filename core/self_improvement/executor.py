"""
core/self_improvement/executor.py — carries out a plan inside an isolated
git workspace: asks Gemini (SMART tier) to rewrite each target file given
the plan, and to write a matching pytest test. Every write goes through
safety_guard first, so a plan that slipped past the earlier checks (or one
called directly, bypassing engine.py) still cannot touch a denied path.
"""
from __future__ import annotations

from pathlib import Path

from core import gemini
from core.self_improvement import safety_guard


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip() + "\n"


def _rewrite_file(repo_relative_path: str, current_content: str, improvement_plan: dict) -> str:
    prompt = f"""You are implementing an approved code change to an existing Python
file in a larger project. Rewrite the COMPLETE file with the change applied -
keep every part of the file unrelated to this change as close to the
original as possible.

FILE: {repo_relative_path}
CHANGE TO MAKE: {improvement_plan.get('change_description', '')}

CURRENT FILE CONTENT:
{current_content}

Return ONLY the complete new file content. No markdown fences, no
commentary, no explanation before or after - just the raw file content."""

    new_content = gemini.text(prompt, tier=gemini.SMART, timeout_ms=60000, default="")
    return _strip_fences(new_content) if new_content else ""


def _write_test(improvement_plan: dict, target_files: list[str]) -> tuple[str, str]:
    prompt = f"""Write a pytest test file that verifies this specific behavior in a
Python project.

WHAT TO VERIFY: {improvement_plan.get('test_description', '')}
CHANGE BEING TESTED: {improvement_plan.get('change_description', '')}
FILES INVOLVED: {target_files}

Return ONLY valid JSON, no markdown fences:
{{"test_file_path": "tests/test_<short_descriptive_name>.py", "test_content": "the complete pytest file content, as a single string with real newlines"}}"""

    result = gemini.as_json(prompt, tier=gemini.SMART, timeout_ms=45000, default=None)
    if not result or not result.get("test_file_path") or not result.get("test_content"):
        return "", ""
    return result["test_file_path"], result["test_content"]


def execute(workspace_path: Path, improvement_plan: dict) -> dict:
    """Applies `improvement_plan` inside workspace_path.

    Returns {"files_changed": [str, ...], "test_file": str|None}.
    Raises safety_guard.SafetyViolation, without writing anything for the
    file that triggered it, if any target or test file is on the denylist."""
    target_files = improvement_plan.get("target_files", [])
    for rel in target_files:
        safety_guard.check_path(rel)

    files_changed = []
    for rel in target_files:
        file_path = workspace_path / rel
        current = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        new_content = _rewrite_file(rel, current, improvement_plan)
        if not new_content.strip():
            continue
        safety_guard.check_content(rel, new_content)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(new_content, encoding="utf-8")
        files_changed.append(rel)

    test_file, test_content = _write_test(improvement_plan, target_files)
    if test_file and test_content:
        safety_guard.check_content(test_file, test_content)
        test_path = workspace_path / test_file
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(test_content, encoding="utf-8")
    else:
        test_file = None

    return {"files_changed": files_changed, "test_file": test_file}
