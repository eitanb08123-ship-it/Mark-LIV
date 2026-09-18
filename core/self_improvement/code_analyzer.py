"""
core/self_improvement/code_analyzer.py — reads the source relevant to a
reported problem and asks Gemini (SMART tier) to identify the root cause.

Read-only: this module never writes anything, and its output is a
DESCRIPTION for planner.py to act on, not code.
"""
from __future__ import annotations

from pathlib import Path

from core import gemini
from memory.config_manager import get_base_dir

BASE_DIR = get_base_dir()
MAX_FILE_CHARS = 20000


def _read_file(rel_path: str) -> str:
    try:
        return (BASE_DIR / rel_path).read_text(encoding="utf-8")[:MAX_FILE_CHARS]
    except Exception as e:
        return f"[could not read {rel_path}: {e}]"


def analyze(problem: str, candidate_files: list[str]) -> dict:
    """Returns {"root_cause": str, "confidence": "low"|"medium"|"high",
    "relevant_files": [str, ...]}, as reported by the model - callers should
    treat this as a hypothesis, not a verified fact."""
    sources = "\n\n".join(f"--- {rel} ---\n{_read_file(rel)}" for rel in candidate_files)

    prompt = f"""You are a senior engineer investigating a bug or limitation report for a
Python desktop AI assistant application. Read the relevant source below and
identify the ROOT CAUSE - not just where the symptom is visible.

PROBLEM REPORT:
{problem}

CANDIDATE SOURCE FILES:
{sources}

Return ONLY valid JSON, no markdown fences:
{{
  "root_cause": "precise technical explanation of why this happens",
  "confidence": "low" | "medium" | "high",
  "relevant_files": ["repo/relative/path.py", ...]
}}"""

    result = gemini.as_json(prompt, tier=gemini.SMART, timeout_ms=45000, default=None)
    if not result or not result.get("root_cause"):
        return {"root_cause": "", "confidence": "low", "relevant_files": candidate_files}
    result.setdefault("relevant_files", candidate_files)
    result.setdefault("confidence", "low")
    return result
