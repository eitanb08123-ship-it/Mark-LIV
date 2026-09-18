# Coding Agent

A real, general-purpose coding agent for **your own projects** — separate
from [SELF_IMPROVEMENT.md](SELF_IMPROVEMENT.md), which only ever touches
Mark LIV's own source through a git-branch-and-approval pipeline built for
the assistant improving *itself*. This one works on plain project folders
inside a sandboxed workspace, and it actually does the work: real files,
real subprocesses, real test runs — not a scripted demo.

## What it can do

Ask naturally, in your own language — it routes through Gemini's normal
tool-calling like every other action:

- **"Create a Python game"**, **"build me a website"**, **"create a new
  project"** — plans the files, writes them, installs dependencies, runs it.
- **"Fix the bug in my project"**, **"check why this doesn't work"** —
  inspects the project, reproduces the failure, reads the error, fixes it.
- **"Add a button to the app"**, **"change the code so that..."** — reads
  the relevant files first, then edits them.
- **"Install a missing dependency"** — runs the package manager, gated like
  any other risky command (see Permissions below).
- **"Review the code and find problems"** — reads and reports, no changes
  required.

## The loop

```
User request
   |
Planning (Gemini decides the next single tool call)
   |
Inspect project (get_project_structure / list_directory / read_file / search_code)
   |
Choose tool
   |
Execute
   |
Test (run_tests / run_command)
   |
Read errors
   |
Fix (write_file / edit_file)
   |
Test again
   |
Finished
```

Implemented in `core/coding_agent/agent_loop.py`. Every iteration hands
Gemini (SMART tier, `core/gemini.py` — the same client `actions/dev_agent.py`
and `core/self_improvement/executor.py` already use) the task, the current
project structure, and a bounded window of recent steps and their results,
and asks for exactly one next tool call. It stops when the model reports
"finished", after too many repeated identical failures in a row, or after a
step-count ceiling — whichever comes first — so a confused loop can't run
forever.

## The tools

| Tool | Module | What it does |
|---|---|---|
| `read_file` | `core/coding_agent/tools.py` | Reads a file's contents (refuses anything past a size ceiling). |
| `write_file` | same | Creates or overwrites a file. |
| `edit_file` | same | Rewrites an *existing* file in full — the same whole-file-rewrite convention `core/self_improvement/executor.py` already uses, rather than a diff format. |
| `list_directory` | same | Lists one directory's immediate contents. |
| `search_code` | same | Greps for a string across the project (optional path/glob filter). |
| `create_directory` | same | Makes a directory. |
| `delete_file` | same | Soft-deletes into `.jarvis_trash/` inside the project — recoverable. |
| `run_command` | same | Runs a shell command with the project directory as its cwd. |
| `run_tests` | same | Runs pytest via `core/self_improvement/test_runner.py` (reused, not duplicated) — "no tests collected" is a failure, not a pass. |
| `get_project_structure` | same | A depth-limited directory tree, used to orient the model before it changes anything. |

## The workspace sandbox

`core/coding_agent/workspace.py` is an **allowlist**, the mirror image of
`core/self_improvement/safety_guard.py`'s denylist: every tool call resolves
its target path and refuses (`WorkspaceViolation`) unless it lands inside one
configured root — `~/Desktop/JarvisWorkspace` by default, next to
`dev_agent`'s own `~/Desktop/JarvisProjects`. Change it with
`memory.config_manager.save_workspace_root(path)`. This is checked in code on
every single tool call, not asked of the model in a prompt.

## Permissions

Enforced in `core/coding_agent/permissions.py`, not by asking the model
nicely:

| Category | Tools | Default | Notes |
|---|---|---|---|
| **Read** | `read_file`, `list_directory`, `search_code`, `get_project_structure` | Always allowed | Nothing reversible or not is at stake. |
| **Write** | `write_file`, `edit_file`, `create_directory` | Always allowed | Every write registers an undo (`core/undo.py`) — the same "act, don't ask" tradeoff `core/confirm.py` documents for reversible changes. |
| **Execute** | `run_command` | Confirmation required | Unless `memory.config_manager.save_coding_agent_auto_execute(True)`. |
| **Install** | `run_command` recognised as `pip`/`npm`/`apt`/`brew` install | Confirmation required | Its own flag, `save_coding_agent_auto_install(True)`, independent from Execute. |
| **Delete** | `delete_file` | **Always** confirmation required | No auto-flag at all — deleting the wrong file is the single most common "it did the wrong thing" a coding agent can cause. Soft-deleted into `.jarvis_trash/` either way. |

A fixed set of especially destructive command patterns (`sudo`, `rm -rf /`,
disk/format tools, fork bombs, forced pushes, ...) is refused **outright** —
never runs, confirmation or not — the same "no override, ever" rule
`safety_guard.py` applies to Mark LIV's own protected paths.

Confirmation itself reuses `core/confirm.py` exactly the way
`actions/self_improve.py` already does: a gated step returns immediately
with a one-sentence status for the model to say out loud, and only runs once
you press CONFIRM on the on-screen banner. Nothing blocks while it waits.

## Progress and the end-of-task report

Every step logs a short, human-readable line through the same
`player.write_log(...)` convention every other action already uses (visible
in the HUD's activity log) — "checking the project structure...",
"writing main.py...", "running the tests...", "found an error, fixing it...".

When a task finishes (`actions/coding_agent.py`), you get:

- what happened (one line, success / partly done / could not finish)
- which files changed
- which tests ran, and whether they passed
- what's left, if anything is

## What it is not

- It is not `self_improve`/`self_analyze` — those two are read-only or
  git-branch-isolated and only ever touch Mark LIV's own source. This agent
  writes directly into your project folder (with undo, not a git branch).
- It does not replace `dev_agent`, which still exists for "build one new
  project from a description" in one call. This agent additionally handles
  existing projects, multi-step fixes, and arbitrary edits.
- It cannot leave its sandboxed workspace, ever, regardless of what the
  model asks for.

## Running the tests

```
python -m pytest tests/test_coding_agent_tools.py tests/test_coding_agent_permissions.py tests/test_agent_loop.py tests/test_workspace.py
```
