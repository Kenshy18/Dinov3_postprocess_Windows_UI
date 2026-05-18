from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .path_utils import wsl_path_to_unc


def hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }


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
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        bootstrap = f"printf %s {encoded} | base64 -d | bash"
        return [*self.base_args(), "bash", "-lc", bootstrap]

    def run_bash(self, script: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.bash_args(script),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **hidden_subprocess_kwargs(),
        )

    @staticmethod
    def list_distros() -> list[str]:
        completed = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            **hidden_subprocess_kwargs(),
        )
        found = [line.replace("\x00", "").strip(" \r") for line in completed.stdout.splitlines()]
        return [line for line in found if line]

    @staticmethod
    def discover_repo_paths(distro: str, *, timeout: int = 12) -> list[str]:
        script = r"""
set -u
roots=()
[ -n "\${HOME:-}" ] && roots+=("\$HOME")
roots+=("/home" "/mnt/c/Users")
tmp="\${TMPDIR:-/tmp}/dinov3_frontend_candidates_\$\$"
: > "\$tmp"
printf '%s\n' "\$HOME/Dinov3_postprocess" >> "\$tmp"
for root in "\${roots[@]}"; do
  [ -d "\$root" ] || continue
  find "\$root" -maxdepth 3 -type d -name Dinov3_postprocess -print 2>/dev/null >> "\$tmp"
done
sort -u "\$tmp" | while IFS= read -r dir; do
  [ -d "\$dir" ] || continue
  if [ -f "\$dir/.runtime/gui_runtime.env" ] && [ -f "\$dir/apps/qt_ui/run_ui_job.py" ] && [ -f "\$dir/scripts/run_integrated_pipeline.py" ]; then
    printf 'ready\t%s\n' "\$dir"
  elif [ -f "\$dir/apps/qt_ui/run_ui_job.py" ] && [ -f "\$dir/scripts/run_integrated_pipeline.py" ]; then
    printf 'repo\t%s\n' "\$dir"
  fi
done
rm -f "\$tmp"
"""
        completed = subprocess.run(
            ["wsl.exe", "-d", distro.strip() or "Ubuntu", "--", "bash", "-lc", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            return []
        ready: list[str] = []
        repo: list[str] = []
        for raw_line in completed.stdout.splitlines():
            kind, _, path = raw_line.partition("\t")
            path = path.strip()
            if not path:
                continue
            if kind == "ready":
                ready.append(path)
            elif kind == "repo":
                repo.append(path)
        return ready + [path for path in repo if path not in ready]

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

    def validate_runtime(self, runtime: WslRuntime | None = None) -> list[tuple[str, bool, str]]:
        runtime = runtime or self.load_runtime()
        checks: list[tuple[str, bool, str]] = []

        def add(name: str, ok: bool, detail: str = "") -> None:
            checks.append((name, ok, detail))

        script = r"""
set -u
cat <<'EOF' | while IFS='|' read -r name path kind; do
repo|.|d
gui_runtime_env|.runtime/gui_runtime.env|f
runtime_profile|.runtime/runtime_profile.json|f
run_ui_job|apps/qt_ui/run_ui_job.py|f
integrated_pipeline|scripts/run_integrated_pipeline.py|f
artifact_checker|tools/artifacts/check_artifacts.py|f
EOF
  if [ "\$kind" = "x" ]; then
    [ -x "\$path" ]
  elif [ "\$kind" = "d" ]; then
    [ -d "\$path" ]
  else
    [ -f "\$path" ]
  fi
  code=\$?
  if [ "\$code" -eq 0 ]; then
    printf 'ok\t%s\t%s\n' "\$name" "\$path"
  else
    printf 'missing\t%s\t%s\n' "\$name" "\$path"
  fi
done
"""
        completed = self.run_bash(script, timeout=20)
        if completed.returncode != 0:
            add("WSL repo", False, (completed.stderr or completed.stdout).strip())
            return checks
        for raw_line in completed.stdout.splitlines():
            status, _, rest = raw_line.partition("\t")
            name, _, detail = rest.partition("\t")
            add(name or "path", status == "ok", detail)

        import_check = (
            "import importlib.util as u; "
            "mods=['PIL','numpy','cv2','torch']; "
            "missing=[m for m in mods if u.find_spec(m) is None]; "
            "print('missing=' + ','.join(missing)); "
            "raise SystemExit(1 if missing else 0)"
        )
        py_check = self.run_bash(self.quote_command([runtime.python, "-c", import_check]), timeout=20)
        detail = runtime.python
        if py_check.returncode != 0:
            detail = f"{runtime.python} {(py_check.stdout or py_check.stderr).strip()}"
        add("runtime_python_imports", py_check.returncode == 0, detail)

        artifacts = self.run_bash(
            "source .runtime/gui_runtime.env; " + self.quote_command([runtime.python, "tools/artifacts/check_artifacts.py", "--require-trt"]),
            timeout=60,
        )
        detail = "check_artifacts.py --require-trt"
        if artifacts.returncode != 0:
            detail = (artifacts.stdout or artifacts.stderr or detail).strip()
        add("runtime_artifacts", artifacts.returncode == 0, detail)
        return checks

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

    def job_script_command(self, script: str) -> list[str]:
        return self.bash_args("set -euo pipefail; source .runtime/gui_runtime.env; " + script)

    def setup_summary(self) -> str:
        try:
            runtime = self.load_runtime()
        except Exception as exc:
            return f"WSL未接続: {exc}"
        recs = runtime.profile_recommendations()
        dinov3 = recs.get("dinov3", {}) if isinstance(recs.get("dinov3"), dict) else {}
        eva02 = recs.get("eva02", {}) if isinstance(recs.get("eva02"), dict) else {}
        codino = recs.get("codino", {}) if isinstance(recs.get("codino"), dict) else {}
        summary = (
            f"WSL={self.distro} repo={self.repo_path} | "
            f"python={runtime.python} | "
            f"DINOv3 batch={dinov3.get('batch_size', '既定')} | "
            f"EVA02 batch={eva02.get('batch_size', '既定')} | "
            f"Co-DINO batch={codino.get('batch_size', '既定')}"
        )
        return summary

    def validation_summary(self, runtime: WslRuntime | None = None) -> str:
        runtime = runtime or self.load_runtime()
        lines = [self.setup_summary()]
        for name, ok, detail in self.validate_runtime(runtime):
            lines.append(f"{'OK' if ok else 'NG'} {name}: {detail}")
        return "\n".join(lines)
