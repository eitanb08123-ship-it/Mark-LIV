"""
core/claude_client.py — the coding agent's "brain": a thin Claude (Anthropic)
client with real tool-use support.

Deliberately separate from core/gemini.py (JARVIS's voice/Live model and
every other action's planning calls, e.g. dev_agent.py,
core/self_improvement/executor.py) and from core/llm_client.py (an unused,
Ollama/LM-Studio-oriented local-model client left over from an earlier
project - its wire format is OpenAI-style function calling over a local HTTP
server, which has nothing to do with Anthropic's Messages API tool-use
blocks). core/coding_agent/agent_loop.py is the only caller of this module;
nothing else in JARVIS changes.

The API key is NEVER read from config/api_keys.json and NEVER hardcoded -
only from the ANTHROPIC_API_KEY environment variable. Model and timeout ARE
configurable, via memory/config_manager.py's usual get_X/save_X convention -
see CODING_AGENT.md.
"""
from __future__ import annotations

import os
from typing import Optional

try:
    import anthropic
except ImportError:          # SDK not installed - is_configured() reports this cleanly
    anthropic = None

from memory.config_manager import get_claude_model, get_claude_timeout_ms

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_TIMEOUT_MS = 60_000
DEFAULT_MAX_TOKENS = 4096

_ENV_KEY = "ANTHROPIC_API_KEY"
_client_cache: Optional["anthropic.Anthropic"] = None


def is_configured() -> bool:
    """True only if the SDK is installed AND the environment variable is set -
    never true because of anything in config/api_keys.json."""
    return anthropic is not None and bool(os.environ.get(_ENV_KEY, "").strip())


def why_not_configured() -> str:
    """A user-facing reason is_configured() is False. '' if it is True."""
    if anthropic is None:
        return "The 'anthropic' package is not installed (pip install anthropic)."
    if not os.environ.get(_ENV_KEY, "").strip():
        return f"{_ENV_KEY} is not set in the environment."
    return ""


def _client() -> "anthropic.Anthropic":
    global _client_cache
    if anthropic is None:
        raise RuntimeError("The 'anthropic' package is not installed - run: pip install anthropic")
    key = os.environ.get(_ENV_KEY, "").strip()
    if not key:
        raise RuntimeError(
            f"{_ENV_KEY} is not set - export it in your environment to let "
            f"the coding agent use Claude as its coding brain."
        )
    if _client_cache is None:
        _client_cache = anthropic.Anthropic(api_key=key)
    return _client_cache


def call_with_tools(system: str, messages: list, tools: list[dict],
                    model: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS,
                    timeout_ms: int | None = None):
    """One Messages API turn with tool-use enabled.

    Returns the SDK's Message object as-is (its `.content` is a list of
    content blocks - text and/or tool_use - and it can be appended straight
    back into `messages` for the next turn). Raises RuntimeError, never the
    SDK's own exception type, so callers only need to catch one thing."""
    cl = _client()
    resolved_model = model or get_claude_model() or DEFAULT_MODEL
    resolved_timeout_ms = timeout_ms if timeout_ms is not None else get_claude_timeout_ms()
    try:
        return cl.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            timeout=max(5.0, resolved_timeout_ms / 1000.0),
        )
    except Exception as e:
        raise RuntimeError(f"Claude call failed: {e}")
