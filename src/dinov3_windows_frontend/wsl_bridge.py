from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .path_utils import wsl_path_to_unc


@dataclass(frozen=True)
class WslRuntime:
    distro: str
    repo_path: str
    gui_runtime_env: dict[str, str]
    runtime_profile: dict

    @property
    def python(self) -> str:
        return self.gui_runtime_env.get("GUI_RUNTIME_PYTHON") or str(Path(self.repo_path) / ".venv_integrated/bin/python")

    def profile_recommendations(self) -> dict:
        recs = self.runtime_profile.get("recommendations", {})
        return recs if isinstance(recs, dict) else {}

    def repo_unc(self) -> str:
        return wsl_path_to_unc(self.distro, self.repo_path)


class WslBridge:
    def __init__(self, distro: str, repo_path: str) -> None:
        self.distro = distro.strip() or "Ubuntu"
        self.repo_path = repo_path.rstrip("/") or "/home/kenke/Dinov3_postprocess"

    def base_args(self) -> list[str]:
        return ["wsl.exe", "-d", self.distro, "--cd", self.repo_path, "--"]

    def bash_args(self, script: str) -> list[str]:
        return [*self.base_args(), "bash", "-lc", script]

    def run_bash(self, script: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(self.bash_args(script), capture_output=True, text=True, timeout=timeout, check=False)

    def quote_command(self, command: list[str]) -> str:
        return " ".join(shlex.quote(part) for part in command)

    def parse_env(self, text: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            try:
                tokens = shlex.split(line, comments=True, posix=True)
            except ValueError:
                tokens = [line]
            if not tokens or "=" not in tokens[0]:
                continue
            key, value = tokens[0].split("=", 1)
            values[key] = value
        return values

    def load_runtime(self) -> WslRuntime:
        script = r"""
set -euo pipefail
test -d .
test -f .runtime/gui_runtime.env
cat .runtime/gui_runtime.env
printf '\n__PROFILE_JSON_BEGIN__\n'
if [ -f .runtime/runtime_profile.json ]; then
  cat .runtime/runtime_profile.json
else
  printf '{}'
fi
"""
        completed = self.run_bash(script)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "WSL runtime check failed").strip())
        env_text, _, profile_text = completed.stdout.partition("__PROFILE_JSON_BEGIN__")
        env = self.parse_env(env_text)
        try:
            profile = json.loads(profile_text.strip() or "{}")
        except json.JSONDecodeError:
            profile = {}
        return WslRuntime(
            distro=self.distro,
            repo_path=self.repo_path,
            gui_runtime_env=env,
            runtime_profile=profile if isinstance(profile, dict) else {},
        )

    def check_artifacts_command(self, runtime: WslRuntime) -> list[str]:
        inner = [
            runtime.python,
            "tools/artifacts/check_artifacts.py",
            "--require-trt",
        ]
        return self.bash_args("set -euo pipefail; source .runtime/gui_runtime.env; " + self.quote_command(inner))

    def job_command(self, inner_command: list[str]) -> list[str]:
        script = "set -euo pipefail; source .runtime/gui_runtime.env; " + self.quote_command(inner_command)
        return self.bash_args(script)

    def setup_summary(self) -> str:
        try:
            runtime = self.load_runtime()
        except Exception as exc:
            return f"WSL未接続: {exc}"
        recs = runtime.profile_recommendations()
        dinov3 = recs.get("dinov3", {}) if isinstance(recs.get("dinov3"), dict) else {}
        eva02 = recs.get("eva02", {}) if isinstance(recs.get("eva02"), dict) else {}
        codino = recs.get("codino", {}) if isinstance(recs.get("codino"), dict) else {}
        return (
            f"WSL={self.distro} repo={self.repo_path} | "
            f"python={runtime.python} | "
            f"DINOv3 batch={dinov3.get('batch_size', '既定')} | "
            f"EVA02 batch={eva02.get('batch_size', '既定')} | "
            f"Co-DINO batch={codino.get('batch_size', '既定')}"
        )
