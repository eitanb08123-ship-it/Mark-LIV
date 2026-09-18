# Self-Improvement Engine

Mark LIV can analyze its own code, propose a fix for a specific problem, try
it in isolation, test it, and only keep it if the test passes and an
independent review agrees. This document explains how it works, how to use
it, and exactly what it cannot do.

## How it works

```
OBSERVE -> IDENTIFY PROBLEM -> ANALYZE -> PLAN -> CREATE EXPERIMENT ->
IMPLEMENT -> TEST -> EVALUATE -> PASS -> (approval?) -> PROMOTE
                                     \-> FAIL -> ROLLBACK
                                          -> LOG RESULT
```

| Stage | Module | What it does |
|---|---|---|
| Identify problem | `core/self_improvement/problem_detector.py` | Takes an explicit problem, or scans `actions/`/`plugins/` for anything that currently fails to load. |
| Analyze | `core/self_improvement/code_analyzer.py` | Reads the relevant source and asks Gemini (SMART tier) for the root cause. Read-only. |
| Plan | `core/self_improvement/planner.py` | Turns the root cause into ONE scoped plan: target file(s), the change, a test description, a risk level. Told about past failed attempts at the same problem so it doesn't repeat them blind. |
| Isolate | `core/self_improvement/rollback_manager.py` | Creates a new git branch + worktree off HEAD (`self-improvement/<timestamp>-<slug>`). Your checked-out branch is never edited directly. |
| Implement | `core/self_improvement/executor.py` | Asks Gemini to rewrite the target file(s) and write a pytest test, inside the isolated worktree only. Every write is checked against `safety_guard` first. |
| Test | `core/self_improvement/test_runner.py` | Runs pytest against the new test (or the whole suite). "No tests collected" is treated as a failure, not a pass. |
| Evaluate | `core/self_improvement/evaluator.py` | A **separate** Gemini call - not the one that wrote the fix - judges the diff and test output. Can't rubber-stamp its own work. |
| Promote/Rollback | `rollback_manager.promote()` / `.discard()` | Pass: branch is kept for review (or immediately merged conceptually via approval - see below). Fail: worktree and branch are both deleted; nothing remains. |
| Log | `core/self_improvement/history.py` | Every attempt - success, failure, rollback, or block - is recorded in `memory/self_improvement_history.json`, with the branch name, root cause, files changed, and reasoning. |

## Using it

Ask naturally - both route through Gemini's normal tool-calling, exactly like
every other action:

- **"Find a way to improve yourself"**, **"what's wrong with you"**, **"check
  why you fail at X"** -> `self_analyze`. Read-only report, no code changes.
- **"Fix X"**, **"figure out why X doesn't work and fix it"** -> `self_improve`.
  Runs the full loop for that specific problem.
- **"What have you improved"**, **"any pending approvals"** -> `self_improvement_status`.
- **"Approve that"**, **"reject attempt abc123"** -> `self_improvement_resolve`.

## Approval

By default (`AUTO_IMPROVEMENT=false`), any plan the planner marked as needing
review - anything touching permissions, security, credentials, confirmation
or undo behavior, or a broad change - stops at **"awaiting_approval"** after
its own tests already passed and it was independently evaluated as correct.
You'll see an on-screen CONFIRM/CANCEL banner (the same mechanism shutdown
and restart use), or you can resolve it later by id with
`self_improvement_resolve` even if the banner is gone.

Turning `AUTO_IMPROVEMENT` on (`memory.config_manager.save_auto_improvement(True)`,
or a future settings-UI toggle) lets a **low-risk** plan promote itself
straight through once tests pass and evaluation agrees - it does not bypass
the safety guard, and a plan the planner itself flagged as needing review
still stops regardless of this setting.

## What it can never do

Enforced in code (`core/self_improvement/safety_guard.py`), not by asking the
model nicely - a rule that only lives in a prompt is not a rule:

- Edit `core/self_improvement/` itself (its own engine, evaluator, history).
- Edit `config/api_keys.json` (credentials).
- Edit `memory/self_improvement_history.json` (its own audit trail).
- Edit `core/confirm.py` (the approval gate).
- Write any file, anywhere, whose new content mentions an API key, auth
  token, password, secret, credential, or the words `auto_improvement` /
  `safety_guard` - that always stops for manual review instead.

If a proposed change is denied, the attempt is recorded as
`blocked_by_safety_guard` and nothing is written - not even inside the
isolated worktree.

## Rollback in practice

A rejected or failed attempt's branch and worktree are both deleted
(`git worktree remove` + `git branch -D`). Your checked-out branch is never
touched in the first place, since the whole attempt happens in a separate
worktree - "rollback" here mostly means "clean up something that was never
attached to your work", not "undo damage".

## Not retrying the same failure blindly

`history.recent_failed_solutions(signature)` looks up every past attempt at
the same problem (matched by a slug of the problem text) that didn't
succeed, and hands that list to the planner as "already tried, don't repeat
unchanged". It is not a hard block - the planner can still try a variation -
but it can no longer propose the exact same failed fix with no new
information.

## Running the tests

```
pip install -r requirements.txt   # includes pytest now
python -m pytest tests/
```

`tests/test_safety_guard.py`, `test_history.py`, `test_rollback_manager.py`
(real, throwaway git repos - never your actual checkout), and
`test_test_runner.py` exercise each module directly. `test_engine.py` mocks
every collaborator to verify the orchestration logic itself: which path
(success / rolled back / awaiting approval / blocked) each scenario takes,
and that a failure never touches git except through `discard()`.

## Known limits of this first version

- No dedicated UI screen yet - status surfaces through the existing activity
  log, the same way every other action already reports what it did.
- `problem_detector`'s automatic scan (no explicit problem given) can only
  see actions/plugins that fail to *load* right now; there's no persistent
  application log to mine for recurring runtime errors yet.
- `executor` asks for a complete rewritten file rather than a diff/patch,
  matching the pattern `actions/dev_agent.py` already uses elsewhere in this
  project - simpler and more reliable than patch-parsing, at the cost of a
  larger prompt for big files.
