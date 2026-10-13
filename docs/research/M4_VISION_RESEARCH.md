# M4 Vision Research Document: Ultralytics, OpenVINO, and CPU Tracking

- **Date**: 2026-10-13
- **Author**: Autonomous Engineering Team (Researcher Role)
- **Status**: Completed (Awaiting Reviewer Approval)
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
    boxes_xyxy = r.boxes.xyxy.cpu().tolist()  # List[List[float]]: [[x1, y1, x2, y2], ...]
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
        device="intel:cpu",  # or "cpu"
        verbose=False
    )
    ```
  - Ultralytics `AutoBackend` detects the directory with `.xml` + `.bin` and delegates loading and inference to `OpenVINOBackend`.
- Source Link: [Ultralytics OpenVINO Integration](https://docs.ultralytics.com/integrations/openvino/) | [Ultralytics Model Export](https://docs.ultralytics.com/modes/export/) (Access Date: 2026-10-13).

---

## 4. OpenVINO Device Strings for Intel Hardware

### Ultralytics Device String Mappings
Ultralytics upstream documentation explicitly documents the following device strings:
- **Intel CPU**: `"intel:cpu"` (or `"cpu"`)
- **Intel Integrated GPU**: `"intel:gpu"`
- **Intel NPU (AI Boost)**: `"intel:npu"`
- **Automatic Target**: `"AUTO"` (supported via fallback)

### Implementation Mechanism in `ultralytics/nn/backends/openvino.py`
In `OpenVINOBackend.load_model(self, weight)`:
```python
core = ov.Core()
fallback_device = "CPU" if core.available_devices == ["CPU"] else "AUTO"
device_name = fallback_device

if isinstance(self.device, str) and self.device.startswith("intel"):
    device_name = self.device.split(":")[1].upper()
    self.device = torch.device("cpu")
    if not any(d == device_name or d.startswith(f"{device_name}.") for d in core.available_devices):
        LOGGER.warning(f"OpenVINO device '{device_name}' not available. Using '{fallback_device}' instead.")
        device_name = fallback_device
```
1. When `device="intel:cpu"`, the device is parsed as `"CPU"`.
2. When `device="intel:gpu"`, the device is parsed as `"GPU"`.
3. When `device="intel:npu"`, the device is parsed as `"NPU"`.
4. If the specified hardware accelerator is not reported in `core.available_devices`, it logs a warning and falls back to `"CPU"` or `"AUTO"`.
5. If `device="cpu"` is passed, `startswith("intel")` is false, and it selects `fallback_device` (`"CPU"`).

### Other Accelerator Device Strings
- Any direct OpenVINO plugin device name string not following `intel:<target>`: **UNVERIFIED** (the upstream Ultralytics backend code only has special split parsing for strings with the `"intel:"` prefix; strings without it default to fallback device).
- Sources:
  - [Ultralytics OpenVINO Guide](https://docs.ultralytics.com/integrations/openvino/) (Access Date: 2026-10-13)
  - [GitHub Source: ultralytics/nn/backends/openvino.py](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/nn/backends/openvino.py#L41-L58) (Access Date: 2026-10-13)
  - [OpenVINO Device Documentation](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes.html) (Access Date: 2026-10-13)

---

## 5. Resetting Tracker State Between Independent Clips

When tracking consecutive frames within a clip, `persist=True` stores tracker instances across frames in `predictor.trackers`. When starting a new, independent clip, tracker state (Kalman filter covariances, active/lost track lists, ID counters) must be reset to avoid carrying over tracks.

### Tracker State Lifecycle Analysis
In `ultralytics/trackers/track.py` (`on_predict_start`):
```python
trackers = getattr(predictor, "trackers", []) if persist else []
predictor.trackers = trackers + [TRACKER_MAP[cfg.tracker_type](args=cfg) for _ in range(needed - len(trackers))]
```
And in `ultralytics/trackers/basetrack.py`:
```python
class BaseTrack:
    _count = 0
    ...
    @staticmethod
    def reset_id() -> None:
        BaseTrack._count = 0
```

### Approved Reset Methods
1. **Model Predictor Tracker Reset & Global ID Reset**:
   Within the tracker adapter (`UltralyticsPersonTracker.reset()`):
   ```python
   # Clear active tracker instances from predictor
   if hasattr(self.model, "predictor") and self.model.predictor is not None:
       self.model.predictor.trackers = []
   # Reset global track ID counter in Ultralytics BaseTrack
   try:
       from ultralytics.trackers.basetrack import BaseTrack
       BaseTrack.reset_id()
   except ImportError:
       pass
   ```
   When `predictor.trackers` is emptied, the next `.track(..., persist=True)` call recognizes `len(predictor.trackers) == 0` and instantiates a brand new `BYTETracker` instance.
2. **Fresh Model Instantiation**:
   Re-instantiating `YOLO(model_path)` provides a fresh, clean predictor with no attached trackers.
3. Sources:
   - [GitHub Source: ultralytics/trackers/track.py](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/trackers/track.py#L45-L95) (Access Date: 2026-10-13)
   - [GitHub Source: ultralytics/trackers/basetrack.py](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/trackers/basetrack.py#L40-L115) (Access Date: 2026-10-13)
   - [Ultralytics Track Mode Documentation](https://docs.ultralytics.com/modes/track/#persisting-tracks-loop) (Access Date: 2026-10-13)

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
- Per project rules in `docs/prompts/M4_VISION_PROMPT.md` (Step 1, item 6):
  > "Whether `ultralytics` pulls in `opencv-python` and `torch`. If so, we use its OpenCV and do NOT add `opencv-python-headless`."
- Therefore, we will **NOT** add `opencv-python-headless` to `backend/pyproject.toml`.
- Sources:
  - [PyPI Metadata: ultralytics/json](https://pypi.org/pypi/ultralytics/json) (Access Date: 2026-10-13)
  - [GitHub Source: ultralytics/pyproject.toml](https://github.com/ultralytics/ultralytics/blob/main/pyproject.toml) (Access Date: 2026-10-13)

---

## 7. Installing CPU-Only PyTorch on Windows with `uv`

### The Issue with Default PyPI Wheels
By default, installing `torch` from standard PyPI on Windows fetches pre-built binary wheels containing NVIDIA CUDA runtime and cuDNN libraries (often exceeding 2.5 GB download size). On a machine with Intel Core Ultra 7 (no NVIDIA GPU), this introduces unnecessary bloat and overhead.

### PyTorch CPU Wheels
PyTorch maintains dedicated CPU-only wheels on `https://download.pytorch.org/whl/cpu`.
- Verified available wheels on index:
  - `torch-2.9.1+cpu-cp311-cp311-win_amd64.whl`
  - `torchvision-0.29.1+cpu-cp311-cp311-win_amd64.whl`

### Recommended Installation Strategy via `uv`

#### Strategy 1: Explicit CLI Pre-installation with `uv pip` (Recommended for Milestone M4)
To ensure the venv receives the lightweight CPU-only wheel without pulling CUDA:
```powershell
uv pip install --python .\.venv\Scripts\python.exe torch torchvision --index-url https://download.pytorch.org/whl/cpu
```
Once `torch` and `torchvision` CPU are installed in the venv, running:
```powershell
uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,vision]"
```
will recognize that `torch` and `torchvision` requirements are already satisfied and will install `ultralytics` and `openvino` from PyPI without downloading CUDA wheels.

#### Strategy 2: `pyproject.toml` Configuration with `tool.uv`
Alternatively, `backend/pyproject.toml` can define explicit index routing:
```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
torchvision = { index = "pytorch-cpu" }
```
- Setting `explicit = true` prevents `uv` from searching the PyTorch index for any non-PyTorch packages.

### Verification of CPU Build
After installation, verify that CUDA is absent:
```powershell
.\.venv\Scripts\python.exe -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('CUDA version:', torch.version.cuda); assert torch.version.cuda is None, 'CUDA build installed!'"
```
- Expected output:
  - `CUDA available: False`
  - `CUDA version: None`
- Sources:
  - [Astral UV: Integration with PyTorch](https://docs.astral.sh/uv/guides/integration/pytorch/) (Access Date: 2026-10-13)
  - [PyTorch Local Installation Guide](https://pytorch.org/get-started/locally/) (Access Date: 2026-10-13)
  - [PyTorch CPU Index Repository](https://download.pytorch.org/whl/cpu/torch/) (Access Date: 2026-10-13)

---

## 8. Summary of Proposed Pins for Reviewer Approval

| Package | Proposed Pin | Index Source | License | Notes |
|---|---|---|---|---|
| `torch` | `torch==2.9.1+cpu` (or `>=2.0.0`) | `https://download.pytorch.org/whl/cpu` | BSD-3-Clause | CPU-only Windows AMD64 wheel |
| `torchvision` | `torchvision==0.29.1+cpu` (or `>=0.15.0`) | `https://download.pytorch.org/whl/cpu` | BSD-3-Clause | Matches CPU torch |
| `ultralytics` | `ultralytics==8.4.176` | PyPI | AGPL-3.0 | SOTA detector + ByteTrack |
| `openvino` | `openvino==2026.4.1` | PyPI | Apache-2.0 | Intel Core Ultra CPU / GPU / NPU runtime |
| `opencv-python` | Transitive (pulled by `ultralytics`) | PyPI | Apache-2.0 | Do NOT add `opencv-python-headless` |
| Weights | `yolo11n.pt` | Ultralytics GitHub release asset | AGPL-3.0 | Downloaded on first use or placed in `data/models/` |

---

## Conclusion and Stop Point
Step 1 research is complete and verified with primary sources. No packages have been installed and no code files have been modified.
Awaiting reviewer approval of the pinned versions and installation strategy before proceeding to Step 2.
