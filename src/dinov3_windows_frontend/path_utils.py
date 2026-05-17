from __future__ import annotations

import re
from pathlib import Path


VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}


def clean_run_part(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip())
    return cleaned.strip("_") or "video"


def windows_path_to_wsl(path: str | Path) -> str:
    raw = str(path).strip().strip('"')
    if not raw:
        return raw
    raw = raw.replace("\\", "/")
    unc_prefix = "//wsl$/"
    if raw.lower().startswith(unc_prefix):
        parts = raw[len(unc_prefix) :].split("/", 1)
        return "/" + parts[1].lstrip("/") if len(parts) == 2 else "/"
    match = re.match(r"^([A-Za-z]):/(.*)$", raw)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2)
        return f"/mnt/{drive}/{rest}"
    if raw.startswith("/"):
        return raw
    return raw


def wsl_path_to_unc(distro: str, path: str | Path) -> str:
    raw = str(path).replace("\\", "/")
    if raw.startswith("/mnt/") and len(raw) > 7 and raw[6] == "/":
        drive = raw[5].upper()
        rest = raw[7:].replace("/", "\\")
        return f"{drive}:\\{rest}"
    if raw.startswith("/"):
        return f"\\\\wsl$\\{distro}\\" + raw.lstrip("/").replace("/", "\\")
    return raw.replace("/", "\\")


def normalize_windows_path(path: str | Path) -> Path:
    return Path(str(path).strip().strip('"')).expanduser()

