"""
core/self_improvement — Mark LIV's self-improvement engine.

Public entry points (see engine.py):
    analyze(problem=None)  - read-only report, never touches disk or git.
    improve(problem)       - runs the full OBSERVE->...->LOG loop for one
                              problem, isolated in a git worktree.
    approve(entry_id) / reject(entry_id) - resolves an "awaiting_approval"
                              attempt left pending by improve().

See SELF_IMPROVEMENT.md at the repo root for the full design.
"""
from core.self_improvement.engine import analyze, approve, improve, reject

__all__ = ["analyze", "improve", "approve", "reject"]
