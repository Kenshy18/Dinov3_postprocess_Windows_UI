$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    py -3.11 -m venv (Join-Path $Root ".venv")
}
& $Python -m pip install -U pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
$env:PYTHONPATH = Join-Path $Root "src"
& $Python -m dinov3_windows_frontend

