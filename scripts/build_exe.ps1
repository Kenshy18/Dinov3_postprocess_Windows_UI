$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

function Test-PythonCandidate {
    param([string[]]$CommandLine)
    $exe = $CommandLine[0]
    $args = @()
    if ($CommandLine.Count -gt 1) {
        $args = $CommandLine[1..($CommandLine.Count - 1)]
    }
    $check = "import sys; raise SystemExit(0 if (sys.version_info >= (3,10) and sys.version_info < (3,12)) else 1)"
    try {
        & $exe @args -c $check *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-BootstrapPython {
    $candidates = @(
        @("py", "-3.11"),
        @("py", "-3.10"),
        @("python"),
        @("python3")
    )
    foreach ($candidate in $candidates) {
        if (Test-PythonCandidate $candidate) {
            return $candidate
        }
    }
    throw "Python 3.10 or 3.11 was not found. Install Python 3.11, or ensure 'py -3.10' / 'python' points to Python 3.10 or 3.11."
}

if (-not (Test-Path $Python)) {
    $Bootstrap = Find-BootstrapPython
    $exe = $Bootstrap[0]
    $args = @()
    if ($Bootstrap.Count -gt 1) {
        $args = $Bootstrap[1..($Bootstrap.Count - 1)]
    }
    & $exe @args -m venv $Venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
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
