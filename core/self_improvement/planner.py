"""
core/self_improvement/planner.py — turns a root cause into ONE small,
scoped plan: which file(s) to change, what the fix is, and how it should be
verified. Never touches the filesystem - executor.py carries the plan out.
"""
from __future__ import annotations

from core import gemini

_KINDS = (
    "bug_fix", "performance", "reliability", "tool_improvement",
    "plugin_improvement", "memory_improvement", "error_handling",
    "ux_improvement", "code_quality",
)


def plan(problem: str, root_cause: str, relevant_files: list[str], past_failures: list[dict]) -> dict:
    failures_text = "\n".join(
        f"- Tried: {f.get('solution', '?')} -> {f.get('status', '?')}: {f.get('reason', '?')}"
        for f in past_failures
    ) or "(none)"

    prompt = f"""You are planning ONE small, isolated code improvement for a Python
desktop AI assistant. Do not propose changes to more files, or a broader
rewrite, than the problem actually requires.

PROBLEM: {problem}
ROOT CAUSE: {root_cause}
FILES BELIEVED RELEVANT: {relevant_files}
PREVIOUSLY TRIED FOR THIS SAME PROBLEM (do not repeat one of these unchanged):
{failures_text}

Return ONLY valid JSON, no markdown fences:
{{
  "kind": one of {list(_KINDS)},
  "summary": "one short sentence describing the fix, usable as a branch/commit name",
  "target_files": ["repo/relative/path.py", ...],
  "change_description": "precise description of what should change and why - "
                         "detailed enough for another engineer to implement it "
                         "without seeing this conversation",
  "test_description": "what a test verifying this fix should check",
  "risk": "low" | "medium" | "high",
  "requires_human_approval": true | false
}}
Set requires_human_approval=true for anything touching permissions, security,
credentials, confirmation/undo behavior, or a broad/architectural change."""

    result = gemini.as_json(prompt, tier=gemini.SMART, timeout_ms=45000, default=None)
    if not result or not result.get("target_files"):
        return {}
    result.setdefault("kind", "bug_fix")
    result.setdefault("risk", "medium")
    result.setdefault("requires_human_approval", result.get("risk") != "low")
    result.setdefault("summary", problem[:60])
    return result
