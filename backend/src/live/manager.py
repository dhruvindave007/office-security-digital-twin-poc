"""Starts/stops all live cameras of a site and serves their status and annotated pictures."""
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .config import LiveConfig, load_live_config
from .entry_gate import EntryGate
from .projector import SiteProjector
from .source import FrameSource
from .worker import CameraWorker

logger = logging.getLogger("office_vision.live.manager")
_COLORS = [(66, 135, 245), (12, 83, 201), (82, 196, 103), (18, 156, 226), (180, 90, 200), (60, 60, 230), (200, 170, 40)]


class LiveManager:
    def __init__(self, settings, twin_engine, rule_engine,
                 tracker_builder: Optional[Callable[[Any], Any]] = None, source_factory=None):
        self.settings = settings
        self.twin = twin_engine
        self.rules = rule_engine
        self.config_path: Optional[str] = getattr(settings, "live_config_path", "") or None
        # tracker_builder(cam_cfg) -> tracker; default builds the Ultralytics tracker for that camera
        self.tracker_builder = tracker_builder or self._default_tracker
        self.source_factory = source_factory or (lambda c: FrameSource(c.camera_id, c.url, realtime=c.realtime, loop=c.loop))
        self.config: Optional[LiveConfig] = None
        self.workers: Dict[str, CameraWorker] = {}
        self.started_at: Optional[str] = None
        self._lock = threading.Lock()
        self.incident_listeners: List[Callable[[List[Any]], None]] = []
        self._jpeg_cache: Dict[str, Any] = {}
        self._saved_hooks = None

    def _default_tracker(self, cam):
        try:
            from src.runtime import make_tracker
        except ImportError:
            from runtime import make_tracker
        return make_tracker(self.settings, model=cam.model, device=cam.device)

    @property
    def running(self) -> bool:
        return bool(self.workers)

    def start(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if self.workers:
                raise RuntimeError("live cameras are already running; stop them first")
            path = config_path or self.config_path
            if not path:
                raise ValueError("no live camera config: set LIVE_CONFIG_PATH or pass config_path")
            cfg = load_live_config(path)
            projector = SiteProjector.from_files(cfg.calibration_path, self.twin.store) if cfg.calibration_path else None
            unknown = [c.camera_id for c in cfg.cameras if c.feed_twin and (projector is None or not projector.knows(c.camera_id))]
            if unknown:
                raise ValueError(f"cameras {unknown} feed the twin but have no calibration")
            known_cams = {c.camera_id for c in self.twin.store.get_cameras()}
            missing = [c.camera_id for c in cfg.cameras if c.feed_twin and c.camera_id not in known_cams]
            if missing:
                raise ValueError(f"cameras {missing} are not in the twin's floor plan (camera table)")
            gate = EntryGate(cfg.entry_gates) if cfg.entry_gates else None
            corr = getattr(self.rules, "correlation", None)
            if gate is not None and corr is not None:
                self._saved_hooks = (corr.entry_filter, corr.entry_confirm)
                corr.entry_filter, corr.entry_confirm = gate.entry_filter, gate.entry_confirm
            workers = {}
            for cam in cfg.cameras:
                src = self.source_factory(cam)
                workers[cam.camera_id] = CameraWorker(
                    cam, src, (lambda c=cam: self.tracker_builder(c)) if cam.detect else None, projector,
                    self.twin, self.rules, cfg.process_fps, cfg.track_exit_seconds, on_incidents=self._emit,
                    entry_gate=gate)
            for w in workers.values():
                w.source.start()
                w.start()
            self.config, self.config_path, self.workers = cfg, path, workers
            self.started_at = datetime.now(timezone.utc).isoformat()
            logger.info("live: started %d cameras from %s", len(workers), path)
            return self.status()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            workers, self.workers = self.workers, {}
        for w in workers.values():
            w.source.stop()
        for w in workers.values():
            w.stop()
        self._jpeg_cache.clear()
        self.started_at = None
        corr = getattr(self.rules, "correlation", None)
        if corr is not None and self._saved_hooks is not None:
            corr.entry_filter, corr.entry_confirm = self._saved_hooks
            self._saved_hooks = None
        return {"stopped": sorted(workers)}

    def _emit(self, incidents: List[Any]) -> None:
        for fn in list(self.incident_listeners):
            fn(incidents)

    def status(self) -> Dict[str, Any]:
        cams = [w.status() for w in self.workers.values()]
        busy = [c for c in cams if c["state"] == "running" and c["detect"]]
        return {"running": self.running, "config_path": self.config_path, "started_at": self.started_at,
                "process_fps": self.config.process_fps if self.config else None,
                "cameras": cams,
                "totals": {"cameras": len(cams), "live": sum(1 for c in cams if c["source"]["state"] == "live"),
                           "people_in_view": sum(c["people_in_view"] for c in cams),
                           "processed_fps": round(sum(c["processed_fps"] for c in busy), 2),
                           "missed_slots": sum(c["missed_slots"] for c in cams)}}

    def jpeg(self, camera_id: str, annotated: bool = True, quality: int = 75, max_width: int = 960) -> Optional[bytes]:
        """Latest processed picture of a camera as JPEG (boxes + track numbers when annotated)."""
        w = self.workers.get(camera_id)
        if w is None:
            return None
        frame, dets, seq, captured_at = w.snapshot()
        if frame is None:
            got = w.source.latest(0)           # not processed yet (or detect=False): raw picture
            if got is None:
                return None
            frame, _, seq = got
            dets = []
        key = (camera_id, seq, annotated, max_width)
        cached = self._jpeg_cache.get(camera_id)
        if cached and cached[0] == key:
            return cached[1]
        import cv2
        img = frame.copy() if annotated else frame
        if annotated:
            for d in dets:
                x, y, bw, bh = d["box"]
                col = _COLORS[d["id"] % len(_COLORS)]
                cv2.rectangle(img, (int(x), int(y)), (int(x + bw), int(y + bh)), col, 2)
                cv2.putText(img, f"#{d['id']}", (int(x) + 3, int(y) + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
        if img.shape[1] > max_width:
            img = cv2.resize(img, (max_width, int(img.shape[0] * max_width / img.shape[1])))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return None
        data = buf.tobytes()
        self._jpeg_cache[camera_id] = (key, data)
        return data
