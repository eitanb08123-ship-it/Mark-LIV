# Coding Agent

A real, general-purpose coding agent for **your own projects** — separate
from [SELF_IMPROVEMENT.md](SELF_IMPROVEMENT.md), which only ever touches
Mark LIV's own source through a git-branch-and-approval pipeline built for
the assistant improving *itself*. This one works on plain project folders
inside a sandboxed workspace, and it actually does the work: real files,
real subprocesses, real test runs — not a scripted demo.

## What it can do

Ask naturally, in your own language. The request itself still reaches
JARVIS through Gemini Live's normal tool-calling like every other action
(`coding_agent` is just another entry in `get_tool_declarations()`) — what
changed is who does the actual programming once the request lands: Claude,
via its own native tool-use (see "The coding brain" below).

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
User request -> JARVIS understands the request (Gemini Live, as usual)
   |
Understand task (Claude)
   |
Inspect project (get_project_structure / list_directory / read_file / search_code)
   |
Plan -> select tool (Claude's native tool-use, not a "reply with JSON" convention)
   |
Execute (core/coding_agent/tools.py, sandboxed + permission-checked)
   |
Inspect result / test (run_tests / run_command)
   |
Read error (stdout/stderr fed straight back to Claude)
   |
Diagnose -> fix (edit_file / write_file)
   |
Test again -> verify
   |
Finished (Claude calls the `finished` tool with a structured report)
```

Implemented in `core/coding_agent/agent_loop.py`. It stops when the brain
calls `finished`, after too many repeated identical failures in a row,
after a step-count ceiling, or if it ever replies without calling any tool
at all (treated as "stuck", not as license to loop forever) — whichever
comes first.

## The coding brain: Claude, with a free Gemini fallback

Claude (`core/claude_client.py`) is the default brain — reads the project,
decides which tool to call, reads the tool's output back, and decides the
next one, using the Claude Messages API's own native tool-use, not a
"reply with JSON and hope it parses" convention.

**If `ANTHROPIC_API_KEY` isn't set, `run_task()` automatically falls back to
Gemini instead of failing** — the same Gemini key every other JARVIS action
already needs, so the coding agent still works with zero extra cost or
setup. Gemini has no tool-use plumbed through `core/gemini.py`, so in
fallback mode it is asked for one JSON decision per turn (`{"tool": ...,
"args": {...}}`) — functionally the same loop, tools, permissions and
sandbox, just a different way of choosing the next step. Both brains are
normalized into the same internal shape before a single shared pipeline
executes them, so nothing downstream (tools, permissions, workspace,
confirmation) needs to know or care which one is driving. Which brain ran
is logged at the start of every task ("Starting task in ... (claude)" or
"... (gemini)").

This is deliberately separate from:

- **`core/llm_client.py`** — an unused, Ollama/LM-Studio-oriented local
  model client left over from an earlier version of this project (different
  wire format entirely: OpenAI-style function calling over a local HTTP
  server). Not a fit for either Claude's or Gemini's actual API, and not
  wired into anything today, so it was left alone rather than repurposed.
- `actions/dev_agent.py` and `core/self_improvement/executor.py`'s own
  one-shot Gemini calls, which are untouched by any of this.

### Configuration

Claude's API key is **never** stored in `config/api_keys.json` and never
hardcoded — only ever read from an environment variable:

```
export ANTHROPIC_API_KEY=sk-ant-...
```

Without it (and with a Gemini key already configured, as JARVIS needs
anyway), the coding agent still works via the free Gemini fallback above —
no extra setup required. Everything else is regular, non-secret
configuration, following the project's usual `get_X()`/`save_X()`
convention in `memory/config_manager.py`:

| Setting | Default | Setter |
|---|---|---|
| Claude model | `core.claude_client.DEFAULT_MODEL` | `save_claude_model(name)` |
| Per-call timeout | 60s | `save_claude_timeout_ms(ms)` |
| Max agent steps | 20 | `save_coding_agent_max_steps(n)` |

If neither `ANTHROPIC_API_KEY` nor a Gemini key is configured (or the
`anthropic` package is not installed), `coding_agent` fails fast with a
clear one-line explanation instead of crashing.

### Bounded context

Neither brain gets an ever-growing transcript. Two things stay small on
purpose:

- The **prompt** is rebuilt every turn from the *current*
  `get_project_structure()` output plus a short one-line-per-step digest
  (last 15 steps) — cheap to regenerate, always up to date. Shared by both
  brains.
- **Claude specifically** also keeps a raw message history (the actual
  `tool_use`/`tool_result` blocks it reasons over turn-to-turn), pruned to
  the last 6 round-trips; older ones are dropped since the live structure
  and digest already cover what they'd otherwise be needed for. Pruning
  always keeps the original task message plus whole (assistant,
  tool_result) pairs, so the conversation Claude sees never violates the
  API's required strict user/assistant alternation. Gemini's one-JSON-
  decision-per-turn convention never accumulates raw history to begin with,
  so it needs no such pruning.

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
  git-branch-isolated, only ever touch Mark LIV's own source, and always run
  on Gemini exactly as before, with no Claude involvement at all.
  `coding_agent` is for projects *you* give it, and its brain is Claude when
  available, Gemini otherwise. Two separate systems, on purpose:
  `self_improve` → JARVIS improves itself. `coding_agent` → JARVIS programs
  your projects.
- It does not replace `dev_agent`, which still exists (on Gemini, unchanged)
  for "build one new project from a description" in one call. This agent
  additionally handles existing projects, multi-step fixes, and arbitrary
  edits.
- It cannot leave its sandboxed workspace, ever, regardless of what the
  model asks for — that boundary lives in `core/coding_agent/workspace.py`
  and is checked in code on every tool call, not asked of Claude in a prompt.
- Neither brain gets unrestricted computer access just because it is doing
  the programming — Claude and Gemini both only ever get the same guarded
  tools in `core/coding_agent/tools.py`, through the same permission gate in
  `core/coding_agent/permissions.py`, as any other caller would.

## Running the tests

```
python -m pytest tests/test_coding_agent_tools.py tests/test_coding_agent_permissions.py tests/test_agent_loop.py tests/test_workspace.py
```

`test_agent_loop.py` mocks only the brain call itself — `core/claude_client.py`'s
`call_with_tools` (with fake Anthropic-shaped content blocks) for the Claude
path, `core/gemini.py`'s `as_json` for the free-fallback path — tool
execution, permission gating, undo, and message-history pruning all run for
real against a temporary workspace, the same way `test_test_runner.py`
exercises a real `pytest` subprocess instead of mocking it.
