from __future__ import annotations

import shlex


PROGRESS_PREFIX = "[phase-progress]"
BATCH_PROGRESS_PREFIX = "[batch-progress]"


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
    if text.startswith(PROGRESS_PREFIX):
        payload = text.removeprefix(PROGRESS_PREFIX).strip()
    elif text.startswith(BATCH_PROGRESS_PREFIX):
        payload = text.removeprefix(BATCH_PROGRESS_PREFIX).strip()
    else:
        return None

    label = None
    if ":" in payload:
        possible_label, rest = payload.split(":", 1)
        if possible_label and "=" not in possible_label:
            label = possible_label.strip()
            payload = rest.strip()

    fields: dict[str, str] = {}
    try:
        tokens = shlex.split(payload)
    except ValueError:
        tokens = payload.split()
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value
    if label and "phase" not in fields:
        fields["phase"] = label
    return fields or None
