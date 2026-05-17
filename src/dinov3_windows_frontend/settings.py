from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


APP_DIR_NAME = "Dinov3PostprocessFrontend"


def app_data_dir() -> Path:
    root = os.environ.get("APPDATA")
    if root:
        path = Path(root) / APP_DIR_NAME
    else:
        path = Path.home() / f".{APP_DIR_NAME}"
    path.mkdir(parents=True, exist_ok=True)
    return path


SETTINGS_PATH = app_data_dir() / "settings.json"


@dataclass
class AppSettings:
    wsl_distro: str = "Ubuntu"
    wsl_repo_path: str = "/home/kenke/Dinov3_postprocess"
    windows_output_dir: str = ""
    run_prefix: str = "ui_run"

    @classmethod
    def load(cls) -> "AppSettings":
        if not SETTINGS_PATH.is_file():
            return cls()
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            data = asdict(cls())
            data.update({key: str(value) for key, value in raw.items() if key in data and value is not None})
            return cls(**data)
        except Exception:
            return cls()

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

