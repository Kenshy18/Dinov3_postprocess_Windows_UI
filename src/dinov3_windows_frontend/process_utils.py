from __future__ import annotations

import base64
import os
from pathlib import Path


def powershell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def hidden_windows_process_command(command: list[str]) -> list[str]:
    if os.name != "nt" or not command:
        return command
    executable = Path(command[0]).name.lower()
    if executable not in {"wsl.exe", "wsl"}:
        return command
    args = ", ".join(powershell_single_quote(part) for part in command[1:])
    script = (
        "$ErrorActionPreference='Continue'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f"& {powershell_single_quote(command[0])} @({args}); "
        "exit $LASTEXITCODE"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        encoded,
    ]
