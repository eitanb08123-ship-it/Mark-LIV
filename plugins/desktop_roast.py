"""
Desktop Roast — a witty, data-driven read of what's currently open and running.

Real numbers (window count, titles, top memory-hungry processes, how long the
oldest running process has been alive) go back as plain data; JARVIS's own
personality (see core/prompt.txt's [VOICE] section) is what turns it into an
actual roast when it speaks the result - this plugin just supplies facts.
"""
from __future__ import annotations

import time

PLUGIN = {
    "name": "desktop_roast",
    "description": (
        "Give a witty, data-backed read of the user's current desktop: how many "
        "windows are open, which ones, the top memory-hungry processes, and how "
        "long the oldest running process has been alive. Use when the user asks "
        "you to 'roast my desktop', 'roast my tabs', comment on how much they "
        "have open, or asks something like 'what's running right now' in a "
        "playful tone. Not for a plain factual hardware report - use the system "
        "monitor tool for that."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        import psutil
    except ImportError:
        return "Sir, desktop_roast needs psutil, which should already be installed - try reinstalling requirements."

    titles: list[str] = []
    try:
        import pygetwindow as gw

        seen_titles = set()
        for w in gw.getAllWindows():
            if w.visible and w.title.strip() and w.title not in seen_titles:
                seen_titles.add(w.title)
                titles.append(w.title)
    except Exception:
        pass  # no window manager reachable (headless, or pygetwindow unsupported here) - skip window data

    procs = []
    try:
        procs = list(psutil.process_iter(["name", "memory_percent", "create_time"]))
    except Exception:
        pass

    memory_hogs = []
    seen_names = set()
    for p in sorted(procs, key=lambda p: p.info.get("memory_percent") or 0, reverse=True):
        name = p.info.get("name") or "unknown"
        if name in seen_names:
            continue
        seen_names.add(name)
        memory_hogs.append((name, p.info.get("memory_percent") or 0))
        if len(memory_hogs) >= 3:
            break

    oldest_age = None
    create_times = [p.info.get("create_time") for p in procs if p.info.get("create_time")]
    if create_times:
        oldest_age = time.time() - min(create_times)

    lines = [f"{len(titles)} window(s) currently open."]
    if titles:
        lines.append("Open windows: " + "; ".join(titles[:12]))
    if memory_hogs:
        hog_text = ", ".join(f"{name} ({pct:.1f}% RAM)" for name, pct in memory_hogs)
        lines.append(f"Top memory users: {hog_text}.")
    if oldest_age is not None:
        lines.append(f"Oldest running process has been alive for {_format_duration(oldest_age)}.")

    result = " ".join(lines)
    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result
