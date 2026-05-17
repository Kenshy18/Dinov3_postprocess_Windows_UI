$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python)) {
    py -3.11 -m venv $Venv
}
& $Python -m pip install -U pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
$env:PYTHONPATH = Join-Path $Root "src"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name Dinov3PostprocessFrontend `
    --paths (Join-Path $Root "src") `
    (Join-Path $Root "src\dinov3_windows_frontend\__main__.py")
Write-Host "Built: $(Join-Path $Root 'dist\Dinov3PostprocessFrontend')"

