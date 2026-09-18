"""
core/self_improvement/engine.py — orchestrates one full improvement attempt:

    OBSERVE -> IDENTIFY PROBLEM -> ANALYZE -> PLAN -> CREATE EXPERIMENT ->
    IMPLEMENT -> TEST -> EVALUATE -> PASS(-> approval? -> PROMOTE) / FAIL(-> ROLLBACK)
    -> LOG RESULT

Two read/write-affecting entry points, called by actions/self_improvement.py:
    analyze(problem=None)  - read-only report, never writes to disk or git.
    improve(problem)       - runs the full loop for one problem.
    approve(entry_id) / reject(entry_id) - resolve an attempt that stopped
                              at "awaiting_approval".
"""
from __future__ import annotations

import re

from core.self_improvement import (
    code_analyzer,
    evaluator,
    executor,
    history,
    planner,
    problem_detector,
    rollback_manager,
    safety_guard,
    test_runner,
)
from memory.config_manager import get_auto_improvement


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "improvement"


def analyze(problem: str | None = None) -> dict:
    """Read-only self-analysis: what's wrong, what's missing, what looks
    risky. Never writes to disk, never touches git. Used by /self-analyze
    and by "find a way to improve yourself"-style requests that should not
    immediately start changing code."""
    candidates = problem_detector.detect(explicit_problem=problem)
    report: dict = {"problems": []}
    for candidate in candidates:
        found = code_analyzer.analyze(candidate["problem"], candidate["files"])
        report["problems"].append({
            "problem": candidate["problem"],
            "root_cause": found.get("root_cause", ""),
            "confidence": found.get("confidence", "low"),
            "relevant_files": found.get("relevant_files", candidate["files"]),
        })
    if not report["problems"]:
        report["summary"] = "No load errors found in actions/plugins right now."
    return report


def improve(problem: str) -> dict:
    """Runs one full improvement attempt for `problem`. Always returns a
    dict with at least {"status": ..., "reason": ...}. Status is one of:
    "success", "awaiting_approval", "rolled_back", "blocked_by_safety_guard",
    "failed"."""
    signature = _slugify(problem)
    past_failures = history.recent_failed_solutions(signature)

    candidate_files = problem_detector.guess_files(problem)
    found = code_analyzer.analyze(problem, candidate_files)
    root_cause = found.get("root_cause", "")
    if not root_cause:
        result = {"status": "failed", "reason": "Could not determine a root cause."}
        history.record({"problem": problem, "problem_signature": signature, **result})
        return result

    relevant_files = found.get("relevant_files", candidate_files)
    plan = planner.plan(problem, root_cause, relevant_files, past_failures)
    if not plan:
        result = {"status": "failed", "reason": "Could not produce a scoped plan.", "root_cause": root_cause}
        history.record({"problem": problem, "problem_signature": signature, **result})
        return result

    try:
        for rel in plan.get("target_files", []):
            safety_guard.check_path(rel)
    except safety_guard.SafetyViolation as e:
        result = {"status": "blocked_by_safety_guard", "reason": str(e)}
        history.record({
            "problem": problem, "problem_signature": signature,
            "solution": plan.get("summary"), "plan": plan, **result,
        })
        return result

    try:
        workspace = rollback_manager.create_workspace(_slugify(plan.get("summary", problem)))
    except rollback_manager.GitError as e:
        result = {"status": "failed", "reason": f"Could not create an isolated workspace: {e}"}
        history.record({
            "problem": problem, "problem_signature": signature,
            "solution": plan.get("summary"), "plan": plan, **result,
        })
        return result

    entry_base = {
        "problem": problem,
        "problem_signature": signature,
        "root_cause": root_cause,
        "solution": plan.get("summary"),
        "plan": plan,
        "branch": workspace.branch,
    }

    try:
        exec_result = executor.execute(workspace.path, plan)
        if not exec_result.get("files_changed"):
            rollback_manager.discard(workspace)
            result = {"status": "failed", "reason": "No file could be rewritten.", **entry_base}
            history.record(result)
            return result

        rollback_manager.commit(workspace, f"self-improve: {plan.get('summary', problem)[:72]}")
        diff_text = rollback_manager.diff(workspace) or rollback_manager.show_last_commit(workspace)
        test_target = exec_result.get("test_file")
        test_result = test_runner.run_tests(workspace.path, target=test_target)
        evaluation = evaluator.evaluate(plan, diff_text, test_result)

        entry_base.update({
            "files_changed": exec_result.get("files_changed", []),
            "test_file": test_target,
            "test_passed": test_result.passed,
        })

        if not evaluation.get("passed"):
            rollback_manager.discard(workspace)
            result = {
                "status": "rolled_back",
                "reason": evaluation.get("reason", "Evaluation failed."),
                **entry_base,
            }
            history.record(result)
            return result

        needs_approval = plan.get("requires_human_approval", True) and not get_auto_improvement()
        if needs_approval:
            result = {
                "status": "awaiting_approval",
                "reason": "Tests passed and the change was evaluated positively; awaiting your approval before it's kept.",
                "diff": diff_text,
                "workspace_path": str(workspace.path),
                **entry_base,
            }
            history.record(result)
            return result

        rollback_manager.promote(workspace)
        result = {
            "status": "success",
            "reason": evaluation.get("reason", "Improvement applied."),
            "diff": diff_text,
            **entry_base,
        }
        history.record(result)
        return result

    except safety_guard.SafetyViolation as e:
        rollback_manager.discard(workspace)
        result = {"status": "blocked_by_safety_guard", "reason": str(e), **entry_base}
        history.record(result)
        return result
    except Exception as e:
        try:
            rollback_manager.discard(workspace)
        except Exception:
            pass
        result = {"status": "failed", "reason": f"Unexpected error: {e}", **entry_base}
        history.record(result)
        return result


def approve(entry_id: str) -> dict:
    """Promotes a pending 'awaiting_approval' attempt: keeps its branch,
    marks it successful. Reconstructs the worktree if the process restarted
    since improve() left it pending."""
    entry = history.get(entry_id)
    if not entry:
        return {"status": "failed", "reason": f"No self-improvement attempt with id '{entry_id}'."}
    if entry.get("status") != "awaiting_approval":
        return {"status": "failed", "reason": f"Attempt '{entry_id}' is not awaiting approval (status: {entry.get('status')})."}

    workspace = rollback_manager.reopen(entry["branch"])
    rollback_manager.promote(workspace)
    history.update_status(entry_id, "success", {"reason": "Approved by user."})
    return {"status": "success", "reason": "Approved and kept.", "branch": entry["branch"]}


def reject(entry_id: str) -> dict:
    """Discards a pending 'awaiting_approval' attempt: removes its branch
    and worktree entirely."""
    entry = history.get(entry_id)
    if not entry:
        return {"status": "failed", "reason": f"No self-improvement attempt with id '{entry_id}'."}
    if entry.get("status") != "awaiting_approval":
        return {"status": "failed", "reason": f"Attempt '{entry_id}' is not awaiting approval (status: {entry.get('status')})."}

    workspace = rollback_manager.reopen(entry["branch"])
    rollback_manager.discard(workspace)
    history.update_status(entry_id, "rejected", {"reason": "Rejected by user."})
    return {"status": "rejected", "reason": "Discarded."}
