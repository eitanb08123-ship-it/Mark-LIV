"""
core/coding_agent — the general-purpose Coding Agent.

Unlike core/self_improvement/ (which patches Mark LIV's OWN source through a
git-branch-and-approval pipeline), this package works on the user's own
projects inside a plain sandboxed workspace folder. See CODING_AGENT.md at
the repo root for the full design.

Public entry point (see agent_loop.py):
    run_task(task, project_path=None, player=None, speak=None) -> dict
"""
from core.coding_agent.agent_loop import run_task

__all__ = ["run_task"]
