"""
core/self_improvement/evaluator.py — decides whether an improvement attempt
actually succeeded.

Deliberately a SEPARATE Gemini call from the one that wrote the fix
(executor.py), given only the diff and the test output - never the
executor's own reasoning trace - so it functions as an independent review
rather than the same process approving its own work.
"""
from __future__ import annotations

from core import gemini
from core.self_improvement.test_runner import TestResult


def evaluate(improvement_plan: dict, diff_text: str, test_result: TestResult) -> dict:
    if not test_result.ran:
        return {"passed": False, "reason": f"Tests could not be run: {test_result.stderr}"}

    if not test_result.passed:
        return {
            "passed": False,
            "reason": f"Tests failed (exit code {test_result.exit_code}).",
            "test_output": (test_result.stdout[-2000:] + test_result.stderr[-2000:]),
        }

    prompt = f"""You are an independent reviewer - you did NOT write this change. Judge
whether the diff below actually implements the intended change correctly and
safely, given that its own tests already pass.

INTENDED CHANGE: {improvement_plan.get('change_description', '')}

DIFF:
{diff_text[:12000]}

TEST OUTPUT (already passing):
{test_result.stdout[-2000:]}

Return ONLY valid JSON, no markdown fences:
{{"passed": true|false, "reason": "one or two sentences", "concerns": ["...", ...]}}"""

    result = gemini.as_json(prompt, tier=gemini.SMART, timeout_ms=45000, default=None)
    if not result:
        # The independent review call itself failed - passing tests alone is
        # not enough to call this a success without that second opinion.
        return {"passed": False, "reason": "Independent evaluation call failed; not approving without it."}
    result.setdefault("passed", False)
    result.setdefault("reason", "")
    return result
