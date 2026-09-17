"""
Day Recap — narrates what the user has been working on today, based on which
files were created or modified in their common folders (Desktop, Documents,
Downloads, Pictures, Videos).

Real filenames and timestamps go back as plain data; JARVIS's own personality
turns it into the actual narration when it speaks the result.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

PLUGIN = {
    "name": "day_recap",
    "description": (
        "Recap what the user has been doing today based on file activity in "
        "their common folders (Desktop, Documents, Downloads, Pictures, "
        "Videos): the first thing they touched today, the most recent, and how "
        "many files changed. Use when the user asks for a 'recap of my day', "
        "'what have I been working on', or something similar - not for "
        "reading the CONTENT of a specific file, which is a different tool."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}

_FOLDERS = ["Desktop", "Documents", "Downloads", "Pictures", "Videos"]
_SKIP_DIR_NAMES = {"node_modules", ".git", "__pycache__", "$recycle.bin"}
_MAX_FILES_SCANNED = 5000


def _today_start_timestamp() -> float:
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def run(parameters: dict, player=None, session_memory=None) -> str:
    home = Path.home()
    today_start = _today_start_timestamp()

    touched: list[tuple[float, Path]] = []
    scanned = 0
    for folder_name in _FOLDERS:
        folder = home / folder_name
        if not folder.exists():
            continue
        try:
            for path in folder.rglob("*"):
                if scanned >= _MAX_FILES_SCANNED:
                    break
                scanned += 1
                if path.is_dir() or any(part.lower() in _SKIP_DIR_NAMES for part in path.parts):
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime >= today_start:
                    touched.append((mtime, path))
        except OSError:
            continue

    if not touched:
        result = (
            "No file activity found today in your Desktop, Documents, Downloads, "
            "Pictures or Videos folders."
        )
    else:
        touched.sort(key=lambda t: t[0])
        first_time, first_path = touched[0]
        last_time, last_path = touched[-1]
        result = (
            f"{len(touched)} file(s) touched today. "
            f"First: '{first_path.name}' at {datetime.fromtimestamp(first_time).strftime('%H:%M')}. "
            f"Most recent: '{last_path.name}' at {datetime.fromtimestamp(last_time).strftime('%H:%M')}."
        )

    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result
