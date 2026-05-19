# Local Work Context - 2026-05-19

This document records the local Windows frontend changes made on this PC before
pushing the `windows-runtime-sync-20260519` branch. The goal is to let reviewers
compare GitHub `main` as the original implementation against this branch as the
current local implementation.

## Repository

- Windows frontend repo: `C:\Inference_front\Dinov3_postprocess_Windows_UI`
- Git remote: `https://github.com/Kenshy18/Dinov3_postprocess_Windows_UI.git`
- WSL runtime inspected during this work:
  `/home/accel/0519/Dinov3_postprocess`
- WSL apps path referenced by the user:
  `\\wsl.localhost\Ubuntu\home\accel\0519\Dinov3_postprocess\apps`

## Initial EXE Build Work

The existing `scripts/build_exe.ps1` already used PyInstaller with `--windowed`,
which suppresses the terminal/console window for a GUI application.

The script did not produce a one-file executable, so it was changed to add:

```powershell
--onefile
```

The build output message was also updated from the one-dir path to:

```text
dist\Dinov3PostprocessFrontend.exe
```

The local machine initially had only Python 3.13 installed. The project requires
Python `>=3.10,<3.12`, so Python 3.11.9 was installed with `winget`:

```powershell
winget install --id Python.Python.3.11 -e --scope user --accept-package-agreements --accept-source-agreements
```

After that, the one-file build succeeded. The generated executable is ignored by
Git via `.gitignore` (`dist/`), so the branch contains the reproducible build
script changes rather than the binary.

## WSL Runtime Alignment Work

The WSL-side `apps/qt_ui` implementation had been changed to:

- remove Co-DINO from the GUI detector choices,
- allow DINOv3 and EVA02 detector Python executables to be specified separately,
- use `DINOV3_DETECTOR_PYTHON`,
- use `EVA02_DETECTOR_PYTHON`,
- forward `EVA02_COMPILE_BACKBONE`,
- prefer runtime environment values from `.runtime/gui_runtime.env`.

The Windows frontend was compared against:

- `apps/qt_ui/app.py`
- `apps/qt_ui/runtime_config.py`
- `apps/qt_ui/run_ui_job.py`
- `.runtime/gui_runtime.env`
- `.runtime/runtime_profile.json`
- `backend/pipeline/run_integrated_pipeline.py`
- detector command builders under `backend/detectors`

## Windows Frontend Changes

### Co-DINO GUI Removal

`Co-DINO` was removed from the Windows detector combo box. The Windows frontend
now exposes:

- `DINOv3`
- `EVA02`
- `顔・頭のみ（AIなし）`

If an older saved UI settings file contains `detector=codino`, command building
falls back to `EVA02` instead of attempting to run an unsupported UI selection.

All Windows-side `codino`/`Co-DINO` references were removed from:

- `src/dinov3_windows_frontend/app.py`
- `src/dinov3_windows_frontend/wsl_bridge.py`
- `src/dinov3_windows_frontend/progress_view.py`
- `README.md`
- `tests/test_path_utils.py`

The WSL backend still contains Co-DINO code, but this Windows GUI branch no
longer exposes or constructs Co-DINO commands.

### Individual Detector Python Environments

The Windows frontend now reads detector-specific Python paths from the loaded
WSL runtime environment:

```text
DINOV3_DETECTOR_PYTHON
EVA02_DETECTOR_PYTHON
```

When present, they are passed to the WSL pipeline as:

```text
--dinov3-python <path>
--eva02-python <path>
```

The runtime inspected on this PC resolved to:

```text
runtime_python=/home/accel/0519/Dinov3_postprocess/.venv_integrated/bin/python
dinov3_python=/home/accel/SOD_Dino_backend_original/.venv_dinov3/bin/python
eva02_python=/home/accel/SOD_Eva_backend_original/.venv/bin/python
```

### EVA02 Compile Mode

The Windows frontend now reads:

```text
EVA02_COMPILE_BACKBONE
```

and forwards it as:

```text
--eva02-compile-backbone <value>
```

The runtime inspected on this PC had:

```text
EVA02_COMPILE_BACKBONE=none
```

### DINOv3 TensorRT Engine Priority

The Windows frontend now prefers the env-defined:

```text
DINOV3_TRT_BACKBONE_ENGINE
```

over the profile recommendation from `.runtime/runtime_profile.json`.

The runtime inspected on this PC resolved to:

```text
/home/accel/SOD_Dino_backend_original/output/trt/dinov3_backbone_fp32_1280x720_dynamic_bf16_forced_b1_8_8.engine
```

### Runtime Validation and Summary

`WslRuntime` now understands:

- shared GUI runtime Python,
- DINOv3 detector Python,
- EVA02 detector Python,
- DINOv3 TensorRT engine,
- EVA02 compile mode.

Connection validation now includes lightweight import checks for the optional
detector-specific Python environments when those paths are configured.

The runtime summary now reports DINOv3/EVA02 Python selections and no longer
prints Co-DINO batch information.

## Verification Performed

Unit tests were run with:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m unittest discover -s tests
```

Result:

```text
Ran 11 tests
OK
```

Compilation check:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m compileall -q src tests
```

Result: success.

The PyInstaller one-file GUI executable was rebuilt successfully:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

Result:

```text
dist\Dinov3PostprocessFrontend.exe
console=False
PyInstaller bootloader: runw.exe
```

## Runtime Error Observed After These Changes

During a GUI-launched run, DINOv3 inference started successfully and used the
expected env-selected TensorRT engine:

```text
[INFO] Using native TensorRT backbone: /home/accel/SOD_Dino_backend_original/output/trt/dinov3_backbone_fp32_1280x720_dynamic_bf16_forced_b1_8_8.engine
[phase-progress] phase=detector detector=dinov3 video=01_h264_aac_progressive.mp4 current=0 total=600 unit=frames percent=0 elapsed=0:00
```

The job then failed inside WSL/PyTorch/Triton compilation:

```text
torch._inductor.exc.InductorError:
RuntimeError: Failed to find C compiler. Please specify via CC environment variable.
```

Important stack trace paths included:

```text
/home/accel/0519/Dinov3_postprocess/backend/detectors/dinov3/runtime/infer_video_dinov3_jsonl.py
/home/accel/SOD_Eva_backend_original/.venv/lib/python3.10/site-packages/torch/_inductor
/home/accel/SOD_Eva_backend_original/.venv/lib/python3.10/site-packages/triton
```

This was classified as a WSL/backend runtime environment issue, not a Windows
frontend GUI issue. The failing process had already been launched in WSL and was
inside detector inference. The immediate cause was that PyTorch Inductor/Triton
could not find a C compiler.

Suggested WSL-side remediation:

```bash
sudo apt update
sudo apt install -y build-essential
```

If needed, set compiler paths in `.runtime/gui_runtime.env`:

```bash
CC=/usr/bin/gcc
CXX=/usr/bin/g++
```

Warnings seen before the fatal error were not considered the direct cause:

- `pkg_resources is deprecated`
- TensorRT default stream performance warning
- `torch.meshgrid` future indexing warning

## Files Changed In This Branch

- `README.md`
- `scripts/build_exe.ps1`
- `src/dinov3_windows_frontend/app.py`
- `src/dinov3_windows_frontend/progress_view.py`
- `src/dinov3_windows_frontend/wsl_bridge.py`
- `tests/test_path_utils.py`
- `docs/LOCAL_WORK_CONTEXT_2026-05-19.md`

