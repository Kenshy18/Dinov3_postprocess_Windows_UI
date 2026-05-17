# DINOv3 Postprocess Windows Frontend

Windows-native Qt frontend for the WSL runtime in `Dinov3_postprocess`.

The Windows app intentionally does not reimplement inference, TensorRT, or
postprocess logic. It launches the already validated WSL commands through
`wsl.exe`, streams stdout/stderr, and renders the existing progress events in
the UI. This keeps detector inference, postprocess, overlay generation,
checkpoint usage, and runtime-profile selection on the WSL side.

## Requirements

- Windows 10/11
- WSL installed
- A working WSL checkout of `Dinov3_postprocess`
- `tools/setup_runtime.sh` already completed in WSL
- Windows Python 3.10 or 3.11 for development/building

## Development Launch

From this Windows-side repository:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m dinov3_windows_frontend
```

On first launch, fill in:

- WSL distro name, for example `Ubuntu`
- WSL runtime repo path, for example `/home/kenke/Dinov3_postprocess`
- Windows output directory

Use **接続確認** before running jobs. The check validates `wsl.exe`, the WSL
repository, `.runtime/gui_runtime.env`, runtime artifacts, and the WSL Python
configured by setup.

## Build EXE

```powershell
.\scripts\build_exe.ps1
```

The executable is written under `dist\Dinov3PostprocessFrontend`.

## Runtime Model

The UI converts Windows paths such as:

```text
C:\Users\name\Videos\input.mp4
```

to WSL paths such as:

```text
/mnt/c/Users/name/Videos/input.mp4
```

and then runs:

```text
wsl.exe -d <distro> --cd <wsl_repo> -- bash -lc '<WSL command>'
```

The command calls WSL-side `apps/qt_ui/run_ui_job.py`, which then calls
`scripts/run_integrated_pipeline.py`. Progress lines such as
`[phase-progress]` are parsed by the Windows UI and shown without changing the
WSL inference/postprocess implementation.

