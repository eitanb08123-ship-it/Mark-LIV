"""
actions/self_analyze.py — read-only self-analysis. Never writes to disk or
git; see core/self_improvement/engine.py:analyze().
"""
from __future__ import annotations

from core.self_improvement import analyze


def _format_report(report: dict) -> str:
    problems = report.get("problems", [])
    if not problems:
        return report.get("summary", "No problems found.")
    lines = [f"Found {len(problems)} problem(s):"]
    for p in problems:
        lines.append(
            f"- {p['problem']} | root cause: {p.get('root_cause') or 'unclear'} "
            f"(confidence: {p.get('confidence', 'low')})"
        )
    return "\n".join(lines)


def self_analyze(parameters: dict, player=None) -> str:
    problem = parameters.get("problem") or None
    try:
        report = analyze(problem)
    except Exception as e:
        return f"Self-analysis failed: {e}"

    result = _format_report(report)
    if player:
        try:
            player.write_log(f"[SELF-IMPROVEMENT] Analysis: {result}")
        except Exception:
            pass
    return result


TOOL = {
    "name": "self_analyze",
    "description": (
        "Analyze the assistant's own code for problems, missing capabilities, "
        "or risky areas - READ ONLY, makes no changes. Use for requests like "
        "'find a way to improve yourself', 'do you have an idea for an "
        "improvement', 'what's wrong with you', or 'check why you fail at X' "
        "when the user wants a report, not an actual fix yet. For an actual "
        "fix, use self_improve instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "problem": {
                "type": "STRING",
                "description": "A specific problem to investigate, if the user named one. Omit to scan generally.",
            },
        },
        "required": [],
    },
    "handler": self_analyze,
}
