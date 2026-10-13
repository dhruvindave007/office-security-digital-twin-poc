# Office Vision Intelligence — CCTV Video Clips Guide

This document describes how to configure, run, and evaluate video clips within the Office Vision Intelligence Platform.

---

## 1. Prerequisites & Environment Setup

To ensure deterministic CPU-only execution without pulling heavy GPU CUDA packages, install the pinned PyTorch CPU wheels and vision dependencies using `uv`:

```powershell
uv pip install --python .\.venv\Scripts\python.exe --index-url https://download.pytorch.org/whl/cpu torch==2.14.1+cpu torchvision==0.29.1+cpu
uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,vision]"
```

Verify that CUDA is disabled and CPU wheels are active:
```powershell
.\.venv\Scripts\python.exe -c "import torch; assert torch.version.cuda is None; assert '+cpu' in torch.__version__; print('Torch CPU verified:', torch.__version__)"
```

---

## 2. Directory Layout & Configuration

Place video files and their corresponding YAML manifests in `data/clips/`:

```
data/clips/
├── lobby_walkthrough.mp4
└── lobby_walkthrough.yaml
```

### Configuration Manifest Schema (`<name>.yaml`)

The manifest filename stem must match the `name` attribute in the YAML document:

```yaml
name: lobby_walkthrough
description: "Recorded walkthrough played as the reception camera."
video_file: lobby_walkthrough.mp4      # relative to data/clips/
camera_id: cam_reception_01            # must exist in TwinStore and calibration
base_time_utc: "2026-01-20T09:30:00Z"  # event time assigned to frame 0
process_fps: 5.0                       # optional; defaults to 5.0 fps
max_frames: null                       # optional integer cap on total frames read
source_note: "Own recording, consented"  # required dataset name/license or consent note
```

---

## 3. Floor Calibration & Spatial Interpretation

> [!NOTE]
> **Illustrative Floor Positions**:
> When an arbitrary evaluation clip is mapped through an office CCTV camera calibration (e.g. `cam_reception_01`), the projected 2D floor coordinates ($x, y$) reflect the mathematical homography of that office camera perspective. Unless the physical camera geometry and scene exactly match the floorplan, resulting coordinates and zone traversals are illustrative for end-to-end pipeline verification.

---

## 4. Dataset Compliance & Licensing

When using external benchmarks or internal recordings, observe the following constraints:

1. **MOT17**:
   - **License**: Creative Commons Attribution-NonCommercial-ShareAlike 3.0 (CC BY-NC-SA 3.0).
   - **Usage**: Internal testing and algorithmic validation only. Never client-facing, commercial, or redistributed without attribution.
2. **PETS 2009 Benchmark Data**:
   - **Usage**: Permitted strictly for academic and non-commercial research evaluation.
3. **Internal Office Recordings**:
   - **Consent & Notice**: Any real footage recorded within corporate facilities requires prior written consent from all identifiable subjects and mandatory CCTV signage in compliance with corporate privacy policy and applicable data privacy regulations (e.g. GDPR / CCPA).

---

## 5. Running Clips via CLI and API

### Command Line Interface

```powershell
# Run a clip end-to-end through the Digital Twin
python -m src.vision.cli run-clip lobby_walkthrough --max-frames 300

# Benchmark detection & tracking across acceleration runtimes
python -m src.vision.cli benchmark --clip lobby_walkthrough --frames 200 --configs torch-cpu,openvino-cpu
```

### REST API

- `GET /api/vision/clips`: List available clips and their presence on disk.
- `POST /api/vision/clips/{name}/run`: Initiate a background run (returns HTTP 202 with `run_id`).
- `GET /api/vision/clips/runs/{run_id}`: Poll background run status and performance metrics.
- `POST /api/vision/clips/runs/{run_id}/cancel`: Gracefully cancel an active background run.
