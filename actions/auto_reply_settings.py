"""
actions/auto_reply_settings.py — conversational on/off switch for the
auto-reply and auto-answer-call features (actions/auto_reply.py,
actions/whatsapp_call_answer.py, actions/instagram_call_answer.py).

WHY THIS FILE EXISTS: those three features are controlled entirely by
memory.config_manager flags (save_auto_reply_enabled(), etc.) - there was
never a TOOL exposing them, so JARVIS had no way to act on a request like
"answer every incoming WhatsApp message" said in plain conversation. It
would either ignore the request or, worse, stall trying to match it to an
unrelated tool. This file is the missing wiring: one tool, callable from
any normal conversation turn, that flips the actual config flags and
reports back the resulting state in plain language - no config file
editing required.

Turning a feature on here only flips the flag; the ~background poll loop
in main.py still has to be running (it's started once at startup, always
on) for the change to take effect - no restart is needed, the next poll
picks up the new flag value.
"""
from __future__ import annotations

from actions.auto_reply import _focus_and_maximize
from memory.config_manager import (
    get_auto_reply_enabled,
    get_auto_reply_platforms,
    get_instagram_auto_answer_enabled,
    get_whatsapp_call_answer_enabled,
    save_auto_reply_enabled,
    save_auto_reply_platforms,
    save_instagram_auto_answer_enabled,
    save_whatsapp_call_answer_enabled,
)

_VALID_PLATFORMS = ("whatsapp", "telegram", "instagram")
_BOOL_FIELDS = ("auto_reply_enabled", "whatsapp_call_answer_enabled", "instagram_call_answer_enabled")

# Instagram is browser-based (no desktop window to bring to the foreground
# the same way - see actions/instagram_call_answer.py's own docstring on
# why it deliberately never touches the user's real focus at all).
_DESKTOP_APP_PLATFORMS = ("whatsapp", "telegram")


def _validate_params(params: dict) -> str:
    """Returns an error message if any PROVIDED field has an invalid
    type, else "". Checked before any setting is written, so a malformed
    call can never partially apply.

    The reason this checks isinstance(value, bool) instead of the old
    bool(value) cast: in Python, bool("false") is True - a payload that
    sends the STRING "false" (a real risk from a malformed API call, not
    a hypothetical) would have silently ENABLED a feature meant to stay
    off. Booleans must be real Python bools now; "true"/"false"/0/1/None/
    lists/dicts are all rejected with a clear message instead of being
    coerced into some guessed meaning."""
    for field in _BOOL_FIELDS:
        if field in params and not isinstance(params[field], bool):
            return f"Invalid value for {field}: expected true or false, got {params[field]!r}."
    if "auto_reply_platforms" in params:
        raw = params["auto_reply_platforms"]
        if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
            return f"Invalid value for auto_reply_platforms: expected a list of platform names, got {raw!r}."
    return ""


def _current_status() -> str:
    reply_state = "on" if get_auto_reply_enabled() else "off"
    reply_platforms = ", ".join(get_auto_reply_platforms())
    wa_calls = "on" if get_whatsapp_call_answer_enabled() else "off"
    ig_calls = "on" if get_instagram_auto_answer_enabled() else "off"
    return (
        f"Auto-reply to messages: {reply_state} (platforms: {reply_platforms}). "
        f"Auto-answer WhatsApp calls: {wa_calls}. "
        f"Auto-answer Instagram calls: {ig_calls}."
    )


def configure_auto_response(parameters: dict, player=None, **_) -> str:
    params = parameters or {}
    # DIAGNOSTIC: this is the ONLY entry point that turns auto-reply on/off
    # (there is no separate "watch mode start/stop" function) - this line
    # is how to confirm the trigger phrase actually reached this tool at
    # all, rather than the model silently not calling it.
    print(f"[AutoReplySettings] configure_auto_response called with: {params}")

    error = _validate_params(params)
    if error:
        return error

    changed = []

    if "auto_reply_enabled" in params:
        enabled = params["auto_reply_enabled"]
        save_auto_reply_enabled(enabled)
        changed.append(f"auto-reply to messages turned {'on' if enabled else 'off'}")

    raw_platforms = params.get("auto_reply_platforms")
    if isinstance(raw_platforms, list) and raw_platforms:
        cleaned = [str(p).strip().lower() for p in raw_platforms]
        cleaned = [p for p in cleaned if p in _VALID_PLATFORMS]
        if cleaned:
            save_auto_reply_platforms(cleaned)
            changed.append(f"auto-reply platforms set to {', '.join(cleaned)}")

    if "whatsapp_call_answer_enabled" in params:
        enabled = params["whatsapp_call_answer_enabled"]
        save_whatsapp_call_answer_enabled(enabled)
        changed.append(f"WhatsApp call auto-answer turned {'on' if enabled else 'off'}")

    if "instagram_call_answer_enabled" in params:
        enabled = params["instagram_call_answer_enabled"]
        save_instagram_auto_answer_enabled(enabled)
        changed.append(f"Instagram call auto-answer turned {'on' if enabled else 'off'}")

    # Bring each currently-configured desktop-app platform's window to the
    # foreground and maximize it ONCE, right when this call activates
    # auto-reply - so Watch Mode's chat list is actually visible on screen.
    # Uses the final platform list (after the block above), so this fires
    # correctly whether platforms were passed in this same call or were
    # already configured from an earlier one.
    if params.get("auto_reply_enabled") is True:
        for platform in get_auto_reply_platforms():
            if platform in _DESKTOP_APP_PLATFORMS:
                focus_result = _focus_and_maximize(platform.title())
                changed.append(focus_result)
                print(f"[AutoReply] {focus_result}")
                if player:
                    player.write_log(f"[AutoReply] {focus_result}")

    status = _current_status()
    if player:
        player.write_log(f"[AutoReplySettings] {status}")

    if not changed:
        return f"Current settings — {status}"
    return f"Done: {'; '.join(changed)}. Current settings — {status}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "configure_auto_response",
    "description": (
        "Turns automatic replies to incoming chat messages, and/or automatic "
        "answering of incoming voice/video calls, on or off for WhatsApp/"
        "Telegram/Instagram - and reports the current settings. Use this "
        "whenever the user asks (in any language) to make JARVIS reply to "
        "messages automatically, answer calls automatically, or asks what "
        "those settings currently are. Passing no parameters just reports "
        "the current status without changing anything. These features send "
        "AI-written replies and accept calls immediately with NO human "
        "review, so only enable what the user actually asked for."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "auto_reply_enabled": {
                "type": "BOOLEAN",
                "description": "Turn automatic replies to incoming messages on (true) or off (false).",
            },
            "auto_reply_platforms": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": (
                    "Which platforms to auto-reply on, ALL AT ONCE - any combination of "
                    "'whatsapp', 'telegram', 'instagram'. Only takes effect while "
                    "auto_reply_enabled is (or already was) true."
                ),
            },
            "whatsapp_call_answer_enabled": {
                "type": "BOOLEAN",
                "description": "Turn automatic answering of incoming WhatsApp calls on (true) or off (false).",
            },
            "instagram_call_answer_enabled": {
                "type": "BOOLEAN",
                "description": "Turn automatic answering of incoming Instagram calls on (true) or off (false).",
            },
        },
    },
    "handler": configure_auto_response,
}
