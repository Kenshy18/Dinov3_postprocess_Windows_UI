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
.\scripts\run_dev.ps1
```

Manual setup, if needed:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m dinov3_windows_frontend
```

Python 3.10 or 3.11 is recommended. The helper scripts try `py -3.11`,
`py -3.10`, `python`, and `python3` in that order.

On first launch, fill in:

- WSL distro name, for example `Ubuntu`
- WSL runtime repo path, for example `/home/kenke/Dinov3_postprocess`
- Windows output directory

The app also tries to discover `Dinov3_postprocess` under the installed WSL
distros on startup. Use **自動探索** to rerun discovery manually, then use
**接続確認** before running jobs. The connection check validates `wsl.exe`, the
WSL repository, `.runtime/gui_runtime.env`, runtime profile, WSL-side UI job
scripts, runtime Python imports, optional DINOv3/EVA02 detector Python
environments, runtime artifacts, and TensorRT artifacts.

The detector selector exposes `DINOv3`, `EVA02`, and `顔・頭のみ（AIなし）`.
If `.runtime/gui_runtime.env` defines `DINOV3_DETECTOR_PYTHON` or
`EVA02_DETECTOR_PYTHON`, the Windows frontend passes them through to
`scripts/run_integrated_pipeline.py` as `--dinov3-python` / `--eva02-python`.
`EVA02_COMPILE_BACKBONE` is also forwarded, and `DINOV3_TRT_BACKBONE_ENGINE`
takes precedence over the profile recommendation for DINOv3.

## Build EXE

```powershell
.\scripts\build_exe.ps1
```

The one-file GUI executable is written to `dist\Dinov3PostprocessFrontend.exe`.

## Generate Codec/Meta Debug Inputs

Use this when you want to stress the Windows frontend and WSL backend with
the same source content but different containers, codecs, and video metadata.
The script writes generated files to `input_meta_debug` and keeps the original
`input` folder untouched.

Preview the plan only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\generate_input_meta_variants.ps1 -AllVideos -ShortTricky
```

Generate the files:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\generate_input_meta_variants.ps1 -AllVideos -ShortTricky -Run -Verify
```

`-AllVideos` creates long batch variants for every input video: H.265 MP4,
MPEG-4 AVI, H.264 MKV, and interlaced-flagged H.264 MP4. `-ShortTricky`
creates heavier edge-case variants only from the shortest source: VFR MKV,
rotation metadata MP4, non-square SAR MP4, and 10-bit 4:2:2 HEVC MP4.
`-IncludeHuge` also adds a ProRes 422 MOV variant, but it can be very large.

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

The Windows frontend stages each job under the WSL repository at:

```text
<wsl_repo>/.runtime/windows_frontend_staging/<run_name>/
```

After the WSL-side job completes successfully, the finished run directory is
copied to the selected Windows output directory and the WSL staging directory is
removed. This keeps detector and postprocess execution on the Ubuntu runtime
while making the user-facing output a normal Windows folder. Text-based summary
and log files are rewritten from staging paths to the final Windows path during
the copy step.
