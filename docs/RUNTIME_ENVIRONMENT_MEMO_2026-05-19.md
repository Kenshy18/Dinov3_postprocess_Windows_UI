# Runtime Environment Memo - 2026-05-19

This memo records the runtime environments involved in the Windows frontend and
WSL backend split on this PC. It is intended to make setup on another RTX 5090
machine easier and to preserve enough context for later root-cause analysis.

## Purpose

The current Windows frontend branch can read `.runtime/gui_runtime.env` and pass
separate detector Python executables to the WSL backend. That makes the GUI
adaptable, but reproducibility still depends on the WSL-side Python, CUDA,
Triton, Detectron2, TensorRT, compiler, and local source states.

For RTX 5090 / sm120-class machines, do not rely on `pip freeze` alone. Capture
both the failing integrated environment and the compromise detector
environments that were selected to keep work moving.

## Environments To Track

| Role | Path | Current Use |
| --- | --- | --- |
| Integrated WSL runtime | `/home/accel/0519/Dinov3_postprocess/.venv_integrated/bin/python` | GUI job wrapper, pipeline, postprocess, TensorRT tooling |
| DINOv3 compromise runtime | `/home/accel/SOD_Dino_backend_original/.venv_dinov3/bin/python` | Passed as `--dinov3-python` |
| EVA02 compromise runtime | `/home/accel/SOD_Eva_backend_original/.venv/bin/python` | Passed as `--eva02-python` |

The original DINOv3 and EVA02 directories are not Git repositories on this PC,
so their file contents must be captured by manifest/hash or archived separately
if another PC must reproduce them exactly.

## Current Machine Snapshot

- WSL distro: Ubuntu 24.04.4 LTS
- Kernel: `6.6.87.2-microsoft-standard-WSL2`
- GPU: NVIDIA GeForce RTX 5090
- Driver: 591.86
- GPU memory: 32607 MiB
- Compute capability: 12.0

Compiler discovery from the interactive shell initially showed no system
`gcc`, `g++`, `cc`, `c++`, or `nvcc` on `PATH`. The runtime env file later added
compiler values under the mamba runtime:

```text
CC=/home/accel/0519/Dinov3_postprocess/.runtime/mamba_py311/bin/x86_64-conda-linux-gnu-gcc
CXX=/home/accel/0519/Dinov3_postprocess/.runtime/mamba_py311/bin/x86_64-conda-linux-gnu-g++
```

These compiler variables are important because the observed failure was a
TorchInductor/Triton compile failure.

## Runtime Env File Values

Relevant values observed in `.runtime/gui_runtime.env`:

```text
GUI_RUNTIME_PYTHON=/home/accel/0519/Dinov3_postprocess/.venv_integrated/bin/python
DINOV3_DETECTOR_PYTHON=/home/accel/SOD_Dino_backend_original/.venv_dinov3/bin/python
EVA02_DETECTOR_PYTHON=/home/accel/SOD_Eva_backend_original/.venv/bin/python
EVA02_DET_PATH=/home/accel/SOD_Eva_backend_original/eva02_det
EVA02_COMPILE_BACKBONE=none
DINOV3_TRT_BACKBONE_ENGINE=/home/accel/SOD_Dino_backend_original/output/trt/dinov3_backbone_fp32_1280x720_dynamic_bf16_forced_b1_8_8.engine
```

`DINOV3_TRT_BACKBONE_ENGINE` appeared twice in the env file. The later value
points to `SOD_Dino_backend_original` and is the value used by shell sourcing.

## Integrated Runtime Details

Path:

```text
/home/accel/0519/Dinov3_postprocess/.venv_integrated/bin/python
```

Observed versions:

```text
Python 3.11.15
pip 26.1.1
torch 2.12.0.dev20260401+cu129
torchvision 0.27.0.dev20260402+cu129
triton 3.7.0
detectron2 0.6
tensorrt 10.13.0.35
cv2 4.8.1
numpy 1.26.4
PIL 12.2.0
setuptools 60.2.0
torch.version.cuda 12.9
cuDNN 92000
GPU capability (12, 0)
pip freeze package count 142
```

Important import path:

```text
detectron2 -> /home/accel/0519/Dinov3_postprocess/eva02/eva02_det/detectron2
```

This means the integrated runtime imports the repo-local EVA02 Detectron2 copy.

## DINOv3 Compromise Runtime Details

Path:

```text
/home/accel/SOD_Dino_backend_original/.venv_dinov3/bin/python
```

Observed versions:

```text
Python 3.10.20
pip 26.1
torch 2.7.1+cu128
torchvision 0.22.1+cu128
triton 3.3.1
detectron2 0.6
tensorrt 10.16.1.11
cv2 4.13.0
numpy 2.2.6
PIL 12.1.1
setuptools 82.0.1
torch.version.cuda 12.8
cuDNN 90701
GPU capability (12, 0)
pip freeze package count 120
```

Important observation:

```text
torch -> /home/accel/SOD_Eva_backend_original/.venv/lib/python3.10/site-packages/torch
torchvision -> /home/accel/SOD_Eva_backend_original/.venv/lib/python3.10/site-packages/torchvision
detectron2 -> /home/accel/SOD_Eva_backend_original/eva02_det/detectron2
```

Although the executable is the DINOv3 venv, several heavy packages resolve from
the EVA02 venv and source tree. This mixing is a key fact for later analysis.

## EVA02 Compromise Runtime Details

Path:

```text
/home/accel/SOD_Eva_backend_original/.venv/bin/python
```

Observed versions:

```text
Python 3.10.20
pip 26.1
torch 2.7.1+cu128
torchvision 0.22.1+cu128
triton 3.3.1
detectron2 0.6
tensorrt not installed
cv2 4.13.0
numpy 2.2.6
PIL 12.1.1
setuptools 80.10.2
torch.version.cuda 12.8
cuDNN 90701
GPU capability (12, 0)
pip freeze package count 104
```

Important import path:

```text
detectron2 -> /home/accel/SOD_Eva_backend_original/eva02_det/detectron2
```

## Runtime Source State

The integrated runtime repo was at:

```text
/home/accel/0519/Dinov3_postprocess
commit 4e220e8
```

It had local modifications in several files, including:

```text
apps/qt_ui/app.py
apps/qt_ui/run_app.sh
backend/detectors/dinov3/commands.py
backend/detectors/eva02/commands.py
backend/pipeline/run_integrated_pipeline.py
eva02/eva02_det/detectron2/layers/roi_align.py
eva02/eva02_det/detectron2/modeling/roi_heads/fast_rcnn.py
tools/setup/setup_integrated_runtime_env.sh
```

The compromise source directories were not Git repositories:

```text
/home/accel/SOD_Dino_backend_original
/home/accel/SOD_Eva_backend_original
```

For another PC, these directories must either be archived as-is or recreated by
a dedicated setup process that also verifies file hashes.

## Observed Failure

The GUI-launched job entered WSL and began DINOv3 detector inference:

```text
[INFO] Using native TensorRT backbone: /home/accel/SOD_Dino_backend_original/output/trt/dinov3_backbone_fp32_1280x720_dynamic_bf16_forced_b1_8_8.engine
[phase-progress] phase=detector detector=dinov3 video=01_h264_aac_progressive.mp4 current=0 total=600 unit=frames percent=0 elapsed=0:00
```

It then failed in TorchInductor/Triton:

```text
torch._inductor.exc.InductorError:
RuntimeError: Failed to find C compiler. Please specify via CC environment variable.
```

This is a WSL/backend runtime issue. The Windows frontend had already launched
the job and passed the configured runtime values.

Warnings present before the fatal error were not the direct cause:

```text
pkg_resources is deprecated
TensorRT default stream warning
torch.meshgrid indexing warning
```

## Root-Cause Questions Preserved For Later

- Why does the DINOv3 venv resolve `torch`, `torchvision`, `numpy`, `PIL`, and
  `detectron2` from the EVA02 environment/source tree?
- Should the DINOv3 compromise environment intentionally depend on EVA02's
  venv, or should it be made self-contained?
- Did `CC`/`CXX` fail to propagate into the launched detector process before
  they were added to `.runtime/gui_runtime.env`?
- Is `build-essential` required in addition to the mamba compiler toolchain?
- Does `EVA02_COMPILE_BACKBONE=none` avoid only backbone compilation while ROI
  Align fallback can still trigger TorchInductor/Triton compilation?
- Should TorchInductor be disabled for the fallback path, or should a compiler
  toolchain be treated as a hard runtime requirement?
- Which local WSL backend modifications are required for sm120 stability and
  should be committed upstream?

## Recommended Setup Strategy For Another RTX 5090 PC

1. Install or import the WSL backend repo.
2. Run the WSL setup script that generates `.runtime/gui_runtime.env`.
3. Ensure compiler availability before detector tests:

```bash
sudo apt update
sudo apt install -y build-essential
```

or provide working `CC`/`CXX` paths in `.runtime/gui_runtime.env`.

4. Rebuild TensorRT engines on the target PC when possible. Do not assume an
   engine from another machine is portable across driver/CUDA/TensorRT changes.
5. Verify the three Python environments separately before running the GUI:

```bash
/home/accel/0519/Dinov3_postprocess/.venv_integrated/bin/python -c "import torch, torchvision, triton, detectron2"
/home/accel/SOD_Dino_backend_original/.venv_dinov3/bin/python -c "import torch, torchvision, triton, detectron2, tensorrt"
/home/accel/SOD_Eva_backend_original/.venv/bin/python -c "import torch, torchvision, triton, detectron2"
```

6. Run the collector script from the Windows frontend repo and compare outputs:

```powershell
.\scripts\collect_wsl_runtime_manifest.ps1
```

The script writes ignored output under `runtime_manifests\...`, including
`pip freeze`, `pip list --format=json`, Git status, runtime env files, and module
import/version reports.

