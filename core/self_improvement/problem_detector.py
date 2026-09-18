"""
core/self_improvement/problem_detector.py — turns either an explicit problem
report (from the user, or from self_improve's caller) or a scan of the
project's own loaders into one or more concrete problem strings, each with a
best-guess list of relevant files for code_analyzer to investigate.

WITHOUT an explicit problem, this can only see what the loaders themselves
can report right now (actions/plugins that fail to import or validate) -
there is no persistent application log file to mine for recurring runtime
errors, so that source is deliberately not claimed here.
"""
from __future__ import annotations

from memory.config_manager import get_base_dir

BASE_DIR = get_base_dir()

_KEYWORD_TO_FILES = {
    "memory": ["memory/memory_manager.py"],
    "config": ["memory/config_manager.py"],
    "undo": ["core/undo.py"],
    "confirm": ["core/confirm.py"],
    "plugin": ["core/plugin_loader.py"],
    "action": ["core/action_loader.py"],
    "voice": ["core/tts.py", "core/stt.py"],
    "wake word": ["core/wake_word.py"],
    "gemini": ["core/gemini.py"],
    "avatar": ["core/avatar.py", "core/avatar_mesh.py"],
}


def guess_files(problem: str) -> list[str]:
    lowered = problem.lower()
    matched: list[str] = []
    for keyword, files in _KEYWORD_TO_FILES.items():
        if keyword in lowered:
            matched.extend(f for f in files if f not in matched)
    return matched or ["main.py"]


def detect(explicit_problem: str | None = None) -> list[dict]:
    """Returns a list of {"problem": str, "files": [str, ...]}.

    Given an explicit problem, returns exactly that one entry. Otherwise,
    scans actions/ and plugins/ for anything that currently fails to load -
    the one class of "known problem" this process can see without a running
    app instance to inspect."""
    if explicit_problem:
        return [{"problem": explicit_problem, "files": guess_files(explicit_problem)}]

    problems: list[dict] = []
    try:
        from core.action_loader import discover_actions

        actions_registry = discover_actions(BASE_DIR / "actions", logger=lambda m: None)
        for rec in actions_registry._all_records:
            if not rec.valid:
                problems.append({
                    "problem": f"Built-in action '{rec.file}' fails to load: {rec.error}",
                    "files": [f"actions/{rec.file}"],
                })
    except Exception as e:
        print(f"[SelfImprovement] action scan failed: {e}")

    try:
        from core.plugin_loader import discover_plugins

        plugins_registry = discover_plugins(BASE_DIR / "plugins", core_tool_names=set(), logger=lambda m: None)
        for rec in plugins_registry._all_records:
            if not rec.valid:
                problems.append({
                    "problem": f"Plugin '{rec.file}' fails to load: {rec.error}",
                    "files": [f"plugins/{rec.file}"],
                })
    except Exception as e:
        print(f"[SelfImprovement] plugin scan failed: {e}")

    return problems
