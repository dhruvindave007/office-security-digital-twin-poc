# M4 Vision Research Document: Ultralytics, OpenVINO, and CPU Tracking

- **Date**: 2026-10-13
- **Author**: Autonomous Engineering Team (Researcher Role)
- **Status**: Approved with Corrections
- **Hardware Profile**: Intel Core Ultra 7 155H (CPU + Intel Arc integrated GPU + Intel AI Boost NPU), 15.5 GB RAM, Windows 11. No NVIDIA GPU / No CUDA.

---

## 1. Latest Stable Versions and Pinned Dependencies

### `ultralytics`
- **Latest Stable Version on PyPI**: `8.4.176`
- **Source Link**: [PyPI: ultralytics](https://pypi.org/project/ultralytics/8.4.176/) | [PyPI JSON API](https://pypi.org/pypi/ultralytics/json)
- **Access Date**: 2026-10-13
- **License**: **AGPL-3.0** (`GNU Affero General Public License v3 or later`, OSI-Approved).
  - License reference: [Ultralytics Licensing](https://github.com/ultralytics/ultralytics/blob/main/LICENSE) (Access Date: 2026-10-13).
- **Exact Pinned Version for Milestone M4**: `ultralytics==8.4.176`

### `openvino`
- **Latest Stable Version on PyPI**: `2026.4.1`
- **Source Link**: [PyPI: openvino](https://pypi.org/project/openvino/2026.4.1/) | [OpenVINO Documentation](https://docs.openvino.ai/2026/index.html)
- **Access Date**: 2026-10-13
- **License**: **Apache-2.0**
- **Wheel Availability**: Verified that `openvino-2026.4.1-22982-cp311-cp311-win_amd64.whl` is published for Python 3.11 on Windows x86_64.
- **Exact Pinned Version for Milestone M4**: `openvino==2026.4.1`

### Model Weights
- **Model Checkpoint**: `yolo11n.pt`
- **Source Link**: [Ultralytics Assets v8.3.0](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt) | [Ultralytics YOLO11 Docs](https://docs.ultralytics.com/models/yolo11/)
- **Access Date**: 2026-10-13
- **Verification**: Verified via HTTP HEAD request returning HTTP 200 OK.

### PyTorch CPU Wheels (Windows cp311 / AMD64)
- **Approved Torch CPU Pin**: `torch==2.14.1+cpu`
- **Approved Torchvision CPU Pin**: `torchvision==0.29.1+cpu`
- **Source Link**: [PyTorch CPU Index](https://download.pytorch.org/whl/cpu) (Access Date: 2026-10-13)
- **Note on pyproject.toml**: `torch` and `torchvision` must **NOT** be added to `backend/pyproject.toml`. They are pre-installed via `uv pip` using the dedicated PyTorch CPU index, and the exact install commands are documented in `docs/VISION_CLIPS.md`.

---

## 2. Python Tracking API across Video Frames

### Tracking Method Call Signature
```python
results = model.track(
    source=frame,
    persist=True,
    tracker="bytetrack.yaml",
    classes=[0],
    conf=0.35,
    device="cpu",
    verbose=False
)
```

### Argument Confirmation
- `source`: The input frame. Accepts a NumPy array (`np.ndarray`, `uint8`, shape `(H, W, 3)` in BGR format from OpenCV), video file path, URL, or stream.
  - Source Link: [Ultralytics Predict Arguments](https://docs.ultralytics.com/modes/predict/#inference-arguments) (Access Date: 2026-10-13).
- `persist`: `bool`. Must be `True` when sequentially processing frames from the same video stream so the tracker retains tracks across frames. If `False`, the tracker is reset before processing.
  - Source Link: [Ultralytics Tracking Loop](https://docs.ultralytics.com/modes/track/#persisting-tracks-loop) (Access Date: 2026-10-13).
- `tracker`: `str`. Path or preset YAML configuration file for the tracker algorithm. Built-in presets include `"bytetrack.yaml"`, `"botsort.yaml"`, `"tracktrack.yaml"`, `"ocsort.yaml"`, `"deepocsort.yaml"`, `"fasttrack.yaml"`.
  - Source Link: [Ultralytics Supported Trackers](https://docs.ultralytics.com/modes/track/#supported-trackers) (Access Date: 2026-10-13).
- `classes`: `list[int]`. Filters detections by category ID. COCO class `0` corresponds to `"person"`. Detections of all other classes are filtered out prior to tracking.
  - Source Link: [Ultralytics Inference Arguments](https://docs.ultralytics.com/modes/predict/#inference-arguments) (Access Date: 2026-10-13).
- `conf`: `float`. Confidence threshold for detector boxes (e.g. `0.35`). Detections with confidence below this threshold are ignored.
  - Source Link: [Ultralytics Inference Arguments](https://docs.ultralytics.com/modes/predict/#inference-arguments) (Access Date: 2026-10-13).
- `device`: `str`. Specifies computation device. For CPU: `"cpu"`. For OpenVINO: `"intel:cpu"`, `"intel:gpu"`, `"intel:npu"`.
  - Source Link: [Ultralytics Inference Arguments](https://docs.ultralytics.com/modes/predict/#inference-arguments) (Access Date: 2026-10-13).
- `verbose`: `bool`. When `False`, suppresses per-frame stdout log lines from the YOLO engine.
  - Source Link: [Ultralytics Inference Arguments](https://docs.ultralytics.com/modes/predict/#inference-arguments) (Access Date: 2026-10-13).

### Reading Results
The `.track()` call returns a list of `Results` objects (length 1 for a single image/frame input). For frame 0:
```python
r = results[0]
if r.boxes is not None and r.boxes.id is not None:
    boxes_xyxy = r.boxes.xyxy.cpu().tolist()   # List[List[float]]: [[x1, y1, x2, y2], ...]
    confidences = r.boxes.conf.cpu().tolist()  # List[float]: [0.92, 0.85, ...]
    track_ids = r.boxes.id.int().cpu().tolist() # List[int]: [1, 2, ...]
```
- `r.boxes.xyxy`: Bounding boxes in absolute pixel coordinates `[x_min, y_min, x_max, y_max]`.
- `r.boxes.conf`: Confidence scores for each bounding box as floats between `0.0` and `1.0`.
- `r.boxes.id`: Tracking IDs for each bounding box as integers.

### Behavior When `boxes.id` is `None`
- In `ultralytics/engine/results.py#L1048-L1061`, property `id` is defined as:
  ```python
  @property
  def id(self) -> torch.Tensor | np.ndarray | None:
      return self.data[:, -3] if self.is_track else None
  ```
- If no objects were tracked in the frame (e.g., all detections are low confidence or in track initialization without an assigned ID), or if tracking is not active (`is_track is False`), `r.boxes.id` evaluates to `None`.
- **Handling**: If `r.boxes is None` or `r.boxes.id is None`, the frame contains zero tracked objects. Detections without an active track ID must be skipped (no `TrackedDetection` emitted).
- Source Link: [Ultralytics Boxes Class Reference](https://docs.ultralytics.com/reference/engine/results/#ultralytics.engine.results.Boxes) | [GitHub Source: ultralytics/engine/results.py](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/engine/results.py#L1040-L1061) (Access Date: 2026-10-13).

---

## 3. OpenVINO Export and Model Loading

### Export Procedure
```python
from ultralytics import YOLO

model = YOLO("data/models/yolo11n.pt")
export_dir = model.export(format="openvino", half=False)
```
- **Export Output Folder Name**:
  - Exporting a weights file named `<stem>.pt` produces a directory named `<stem>_openvino_model/` located in the same parent directory as the input weights file.
  - For `yolo11n.pt`, the exported folder is `yolo11n_openvino_model/`.
- **Export Contents**:
  - `<stem>.xml`: Network topology and layer definitions in OpenVINO IR format.
  - `<stem>.bin`: Binary model weights and biases.
  - `metadata.yaml`: Ultralytics metadata containing model tasks, class names, image dimensions, and version information.
- **Loading the Exported Model for Tracking**:
  - Pass the exported directory path directly into `YOLO()`:
    ```python
    ov_model = YOLO("data/models/yolo11n_openvino_model/")
    results = ov_model.track(
        source=frame,
        persist=True,
        tracker="bytetrack.yaml",
        classes=[0],
        device="intel:cpu",  # or "intel:gpu", "intel:npu"
        verbose=False
    )
    ```
  - Ultralytics `AutoBackend` detects the directory with `.xml` + `.bin` and delegates loading and inference to `OpenVINOBackend`.
- Source Link: [Ultralytics OpenVINO Integration](https://docs.ultralytics.com/integrations/openvino/) | [Ultralytics Model Export](https://docs.ultralytics.com/modes/export/) (Access Date: 2026-10-13).

---

## 4. OpenVINO Device Strings and Silent Fallback Guard

### Ultralytics Device String Mappings
Ultralytics upstream documentation explicitly documents the following device strings:
- **PyTorch CPU**: `"cpu"`
- **Intel CPU (OpenVINO)**: `"intel:cpu"`
- **Intel Integrated GPU (OpenVINO)**: `"intel:gpu"`
- **Intel NPU AI Boost (OpenVINO)**: `"intel:npu"`

### Silent Fallback Risk & Benchmark Guard Rule
In `ultralytics/nn/backends/openvino.py` lines 41-49:
```python
fallback_device = "CPU" if core.available_devices == ["CPU"] else "AUTO"
device_name = fallback_device

if isinstance(self.device, str) and self.device.startswith("intel"):
    device_name = self.device.split(":")[1].upper()
    self.device = torch.device("cpu")
    if not any(d == device_name or d.startswith(f"{device_name}.") for d in core.available_devices):
        LOGGER.warning(f"OpenVINO device '{device_name}' not available. Using '{fallback_device}' instead.")
        device_name = fallback_device
```
**CRITICAL ARCHITECTURAL RULE**: Ultralytics silently falls back to CPU/AUTO with only a warning if the requested Intel accelerator is not present. Therefore, in the Step 7 benchmark suite:
1. For every `openvino-*` configuration, **first** read `openvino.Core().available_devices`.
2. Record `available_devices` in the benchmark JSON under `hardware.openvino_devices`.
3. If the requested target (`CPU`, `GPU`, `NPU`) is not present in `available_devices` (matching exact name or `"NAME."` prefix), record:
   ```json
   {
       "status": "unavailable",
       "available_devices": [...]
   }
   ```
   and **skip** running that configuration.
4. **Never report a benchmark result for an accelerator device that was not physically available.**
- Sources:
  - [Ultralytics OpenVINO Guide](https://docs.ultralytics.com/integrations/openvino/) (Access Date: 2026-10-13)
  - [GitHub Source: ultralytics/nn/backends/openvino.py#L41-L58](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/nn/backends/openvino.py#L41-L58) (Access Date: 2026-10-13)
  - [OpenVINO Device Documentation](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes.html) (Access Date: 2026-10-13)

---

## 5. Resetting Tracker State Between Independent Clips

### Approved Reset Implementation (Option B)
To ensure complete isolation and deterministic execution across independent clips without coupling to private Ultralytics internal fields:
- `UltralyticsPersonTracker.reset()` **creates a fresh `YOLO` instance from the same weights path**.
- **Do NOT touch `predictor.trackers` or `BaseTrack` internals.**
- Creating a fresh `YOLO` instance cleanly initializes a brand new predictor and fresh tracker instances, ensuring no state leakage between video clips.

---

## 6. Dependency Tree: OpenCV and PyTorch

### PyPI Requirements Analysis
Inspection of `https://pypi.org/pypi/ultralytics/json` for `ultralytics==8.4.176` reveals the following mandatory runtime dependencies:
- `opencv-python!=4.13.0.90,>=4.7.0`
- `torch>=1.8.0` (on Windows: `torch!=2.4.0,>=1.8.0; sys_platform == "win32"`)
- `torchvision>=0.9.0`
- `pillow>=7.1.2`
- `pyyaml>=5.3.1`
- `numpy>=1.23.0`
- `psutil>=5.8.0`
- `matplotlib>=3.3.0`

### Consequence for Repository Dependencies
- `ultralytics` **automatically pulls in `opencv-python` and `torch`**.
- Per project rules: we use its bundled `opencv-python` and do **NOT** add `opencv-python-headless`.
- Sources:
  - [PyPI Metadata: ultralytics/json](https://pypi.org/pypi/ultralytics/json) (Access Date: 2026-10-13)
  - [GitHub Source: ultralytics/pyproject.toml](https://github.com/ultralytics/ultralytics/blob/main/pyproject.toml) (Access Date: 2026-10-13)

---

## 7. Installing CPU-Only PyTorch on Windows with `uv`

### Install Protocol
1. Pre-install CPU-only PyTorch and Torchvision wheels:
   ```powershell
   uv pip install --python .\.venv\Scripts\python.exe --index-url https://download.pytorch.org/whl/cpu torch==2.14.1+cpu torchvision==0.29.1+cpu
   ```
2. Install project with development and vision extras:
   ```powershell
   uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,vision]"
   ```
3. Verification:
   ```powershell
   .\.venv\Scripts\python.exe -c "import torch; print('torch version:', torch.__version__); print('torch cuda:', torch.version.cuda); assert torch.version.cuda is None, 'CUDA build installed!'; assert torch.__version__.endswith('+cpu'), 'Not a CPU build!'"
   ```
   If the second command replaced torch with a non-CPU build, STOP and report immediately.

---

## 8. Summary of Approved Pins

| Package / Artifact | Approved Pin | Source Index | License |
|---|---|---|---|
| `torch` | `torch==2.14.1+cpu` | `https://download.pytorch.org/whl/cpu` | BSD-3-Clause |
| `torchvision` | `torchvision==0.29.1+cpu` | `https://download.pytorch.org/whl/cpu` | BSD-3-Clause |
| `ultralytics` | `ultralytics==8.4.176` | PyPI | AGPL-3.0 |
| `openvino` | `openvino==2026.4.1` | PyPI | Apache-2.0 |
| `opencv-python` | Transitive (via `ultralytics`) | PyPI | Apache-2.0 |
| Model weights | `yolo11n.pt` | Ultralytics GitHub release asset | AGPL-3.0 |
