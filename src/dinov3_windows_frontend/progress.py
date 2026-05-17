from __future__ import annotations

import re


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_progress_line(line: str) -> dict[str, str] | None:
    text = line.strip()
    if not text.startswith("[phase-progress]"):
        return None
    payload = text.removeprefix("[phase-progress]").strip()
    fields: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_-]*)=([^ ]+)", payload):
        fields[match.group(1)] = match.group(2)
    return fields or None

