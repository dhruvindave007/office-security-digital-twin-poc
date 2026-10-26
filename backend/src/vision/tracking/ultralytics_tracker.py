import threading
from pathlib import Path
from typing import Any, List

try:
    from src.core.paths import _resolve_data_path
    from src.vision.geometry import BoundingBox2D
    from src.vision.tracking.base import PersonTracker, TrackedDetection
except ImportError:
    from core.paths import _resolve_data_path
    from vision.geometry import BoundingBox2D
    from vision.tracking.base import PersonTracker, TrackedDetection


_OV_CACHE_LOCK = threading.Lock()


def _enable_openvino_cache(cache_dir: Path) -> None:
    """Make every OpenVINO Core (Ultralytics creates its own) keep compiled models in cache_dir.

    Compiling for the GPU/NPU is the slow part of start-up (measured: NPU 34 s -> 3 s, GPU 16 s -> 5 s
    once cached). The cache is keyed by model, device and driver, so a changed model recompiles by itself.
    """
    import openvino as ov
    with _OV_CACHE_LOCK:   # camera workers load their models in parallel threads
        if getattr(ov.Core, "_ovt_cache_dir", None):
            return
        cache_dir.mkdir(parents=True, exist_ok=True)
        original_init = ov.Core.__init__

        def __init__(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            try:
                self.set_property({"CACHE_DIR": str(cache_dir)})
            except Exception:   # a device without cache support still works, just compiles every time
                pass

        ov.Core.__init__ = __init__
        ov.Core._ovt_cache_dir = str(cache_dir)


class UltralyticsPersonTracker(PersonTracker):
    """Ultralytics YOLO tracking adapter implementing the PersonTracker interface."""

    def __init__(
        self,
        model: str,
        device: str,
        tracker_cfg: str,
        min_confidence: float,
        models_dir: str,
    ):
        self.model_name = model
        self.device = device
        self.tracker_cfg = tracker_cfg
        self.min_confidence = min_confidence
        self.models_dir = models_dir

        resolved_dir = _resolve_data_path(models_dir, "data/models")
        resolved_dir.mkdir(parents=True, exist_ok=True)

        model_path = Path(model)
        if model_path.is_absolute() or model_path.parent != Path("."):
            self.weights_path = str(model_path)
        else:
            self.weights_path = str(resolved_dir / model)

        self._load_yolo()

    def _load_yolo(self) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise RuntimeError("Vision extra not installed: uv pip install -e .[vision]") from e

        if str(self.device).startswith("intel"):
            _enable_openvino_cache(Path(self.weights_path).parent / "ov_cache")
        self._model = YOLO(self.weights_path)
        # Exported models (OpenVINO) have a fixed input shape, e.g. 480x640 for 4:3 cameras (25% less
        # work than padding to 640x640). Run inference at that shape; .pt models keep Ultralytics' default.
        self.imgsz = None
        meta = Path(self.weights_path) / "metadata.yaml"
        if meta.is_file():
            import yaml
            self.imgsz = (yaml.safe_load(meta.read_text(encoding="utf-8")) or {}).get("imgsz")

    def warmup(self) -> None:
        """Run one throw-away inference so model compilation (OpenVINO: seconds) happens before live use."""
        import numpy as np
        h, w = (self.imgsz if isinstance(self.imgsz, (list, tuple)) else (self.imgsz or 640,) * 2)[:2]
        self._model.predict(np.zeros((int(h), int(w), 3), np.uint8), device=self.device, verbose=False,
                            **({"imgsz": self.imgsz} if self.imgsz else {}))

    def reset(self) -> None:
        """Reset tracker state by instantiating a fresh YOLO model instance from the weights path (Option B)."""
        self._load_yolo()

    def update(self, frame: Any) -> List[TrackedDetection]:
        """Process a single video frame with persistent tracking and return tracked person detections."""
        results = self._model.track(
            source=frame,
            persist=True,
            tracker=self.tracker_cfg,
            classes=[0],
            conf=self.min_confidence,
            device=self.device,
            verbose=False,
            **({"imgsz": self.imgsz} if self.imgsz else {}),
        )

        if not results:
            return []

        r = results[0]
        if r.boxes is None or r.boxes.id is None:
            return []

        boxes_xyxy = r.boxes.xyxy.cpu().tolist()
        confs = r.boxes.conf.cpu().tolist()
        track_ids = r.boxes.id.int().cpu().tolist()

        detections: List[TrackedDetection] = []
        for box, conf, tid in zip(boxes_xyxy, confs, track_ids):
            if tid is None:
                continue
            x1 = max(0.0, float(box[0]))
            y1 = max(0.0, float(box[1]))
            x2 = max(x1, float(box[2]))
            y2 = max(y1, float(box[3]))
            bbox = BoundingBox2D(u_min=x1, v_min=y1, u_max=x2, v_max=y2)
            detections.append(
                TrackedDetection(
                    local_track_id=int(tid),
                    bbox=bbox,
                    confidence=float(conf),
                )
            )

        return detections

    @property
    def name(self) -> str:
        return f"ultralytics:{self.model_name}:{self.tracker_cfg}"
