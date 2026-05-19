param(
    [string]$Distro = "Ubuntu",
    [string]$RuntimeRepo = "/home/accel/0519/Dinov3_postprocess",
    [string]$DinoRepo = "/home/accel/SOD_Dino_backend_original",
    [string]$EvaRepo = "/home/accel/SOD_Eva_backend_original",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

if (-not $OutputDir) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputDir = Join-Path $Root "runtime_manifests\$stamp"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

function Invoke-WslBash {
    param(
        [Parameter(Mandatory = $true)][string]$Script,
        [string]$OutFile = ""
    )
    $bytes = [Text.Encoding]::UTF8.GetBytes($Script)
    $payload = [Convert]::ToBase64String($bytes)
    $command = "printf %s $payload | base64 -d | bash"
    $args = @("-d", $Distro, "--", "bash", "-lc", $command)
    if ($OutFile) {
        & wsl.exe @args *> $OutFile
    } else {
        & wsl.exe @args
    }
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code $LASTEXITCODE"
    }
}

$commonHeader = @"
set -u
RUNTIME_REPO='$RuntimeRepo'
DINO_REPO='$DinoRepo'
EVA_REPO='$EvaRepo'
"@

Invoke-WslBash -OutFile (Join-Path $OutputDir "system.txt") -Script ($commonHeader + "`n" + @'
printf '%s\n' '## OS'
(lsb_release -a 2>/dev/null || true)
uname -a
printf '\n%s\n' '## GPU'
(nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader 2>/dev/null || nvidia-smi 2>/dev/null || true)
printf '\n%s\n' '## Compiler'
for c in gcc g++ cc c++ nvcc; do
  printf '%s_path=' "$c"; command -v "$c" || true
  "$c" --version 2>/dev/null | head -n 1 || true
done
printf 'CC=%s\nCXX=%s\n' "${CC:-}" "${CXX:-}"
'@)

Invoke-WslBash -OutFile (Join-Path $OutputDir "git_status.txt") -Script ($commonHeader + "`n" + @'
for d in "$RUNTIME_REPO" "$DINO_REPO" "$EVA_REPO"; do
  echo "## $d"
  if [ -d "$d/.git" ]; then
    git -C "$d" rev-parse HEAD
    git -C "$d" status --short
    git -C "$d" diff --stat
  else
    echo "not a git repo"
  fi
  echo
done
'@)

Invoke-WslBash -OutFile (Join-Path $OutputDir "gui_runtime.env") -Script ($commonHeader + "`n" + @'
if [ -f "$RUNTIME_REPO/.runtime/gui_runtime.env" ]; then
  cat "$RUNTIME_REPO/.runtime/gui_runtime.env"
fi
'@)

Invoke-WslBash -OutFile (Join-Path $OutputDir "runtime_profile.json") -Script ($commonHeader + "`n" + @'
if [ -f "$RUNTIME_REPO/.runtime/runtime_profile.json" ]; then
  cat "$RUNTIME_REPO/.runtime/runtime_profile.json"
fi
'@)

$pythonReport = @'
if [ ! -x "$PYTHON_BIN" ]; then
  echo "missing: $PYTHON_BIN"
  exit 0
fi
"$PYTHON_BIN" --version
"$PYTHON_BIN" -m pip --version 2>/dev/null || true
"$PYTHON_BIN" - <<'PY'
import importlib
import json
import os
import platform
import sys

mods = ["torch", "torchvision", "triton", "detectron2", "tensorrt", "cv2", "numpy", "PIL", "setuptools"]
data = {
    "executable": sys.executable,
    "prefix": sys.prefix,
    "python": sys.version,
    "platform": platform.platform(),
    "env": {key: os.environ.get(key) for key in ("CC", "CXX", "PATH", "LD_LIBRARY_PATH", "PYTHONPATH")},
    "modules": {},
    "sys_path_head": sys.path[:12],
}
for name in mods:
    try:
        mod = importlib.import_module(name)
        data["modules"][name] = {
            "version": getattr(mod, "__version__", "unknown"),
            "file": getattr(mod, "__file__", "builtin"),
        }
    except Exception as exc:
        data["modules"][name] = {
            "error": f"{type(exc).__name__}: {exc}",
        }
try:
    import torch

    data["torch_cuda"] = {
        "available": torch.cuda.is_available(),
        "torch_cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
    }
    if torch.cuda.is_available():
        data["torch_cuda"]["device_name"] = torch.cuda.get_device_name(0)
        data["torch_cuda"]["capability"] = torch.cuda.get_device_capability(0)
except Exception as exc:
    data["torch_cuda_error"] = repr(exc)
print(json.dumps(data, ensure_ascii=False, indent=2))
PY
'@

$envs = @(
    @{ Name = "integrated"; Python = "$RuntimeRepo/.venv_integrated/bin/python" },
    @{ Name = "dinov3_original"; Python = "$DinoRepo/.venv_dinov3/bin/python" },
    @{ Name = "eva02_original"; Python = "$EvaRepo/.venv/bin/python" }
)

foreach ($env in $envs) {
    $name = $env.Name
    $python = $env.Python
    Invoke-WslBash -OutFile (Join-Path $OutputDir "$name.python_report.txt") -Script ($commonHeader + "`nPYTHON_BIN='$python'`n" + $pythonReport)
    Invoke-WslBash -OutFile (Join-Path $OutputDir "$name.pip_freeze.txt") -Script ($commonHeader + "`nPYTHON_BIN='$python'`n" + @'
if [ -x "$PYTHON_BIN" ]; then "$PYTHON_BIN" -m pip freeze; fi
'@)
    Invoke-WslBash -OutFile (Join-Path $OutputDir "$name.pip_list.json") -Script ($commonHeader + "`nPYTHON_BIN='$python'`n" + @'
if [ -x "$PYTHON_BIN" ]; then "$PYTHON_BIN" -m pip list --format=json; fi
'@)
}

Write-Host "Runtime manifest written to: $OutputDir"
