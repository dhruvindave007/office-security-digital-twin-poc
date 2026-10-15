"""One worker per camera: newest frame -> detect + track -> floor position -> twin -> rules.

Timing model: the worker wakes `process_fps` times a second and takes the newest frame (older frames
are skipped by design). If a cycle overruns its slot, the missed slots are counted, never queued,
so latency stays bounded instead of growing.
"""
from collections import deque
from datetime import datetime, timedelta, timezone
import logging
import statistics
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional

try:
    from src.core.locks import TWIN_WRITE_LOCK
    from src.core.timeutil import canonical_utc
except ImportError:
    from core.locks import TWIN_WRITE_LOCK
    from core.timeutil import canonical_utc

logger = logging.getLogger("office_vision.live.worker")
_WINDOW = 300  # samples kept for latency statistics (~1 min at 5 fps)


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


class CameraWorker:
    def __init__(self, cam_cfg, source, tracker_factory: Optional[Callable[[], Any]], projector,
                 twin_engine, rule_engine, process_fps: float, track_exit_seconds: float,
                 on_incidents: Optional[Callable[[List[Any]], None]] = None, entry_gate=None):
        self.cfg = cam_cfg
        self.camera_id = cam_cfg.camera_id
        self.source = source
        self.tracker_factory = tracker_factory
        self.projector = projector
        self.twin = twin_engine
        self.rules = rule_engine
        self.period = 1.0 / max(0.1, process_fps)
        self.track_exit_seconds = track_exit_seconds
        self.on_incidents = on_incidents
        self.entry_gate = entry_gate
        self.track_prefix = f"live:{self.camera_id}:"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.tracker = None
        self.state = "stopped"
        self.last_error: Optional[str] = None
        self.frames_processed = 0
        self.missed_slots = 0
        self.incidents_raised = 0
        self.people_in_view = 0
        self.last_detections: List[Dict] = []   # for the annotated live view
        self.last_frame = None
        self.last_frame_seq = 0
        self.last_captured_at: Optional[datetime] = None
        self._lock = threading.Lock()
        self._lat: Dict[str, Deque[float]] = {k: deque(maxlen=_WINDOW) for k in ("detect_ms", "twin_ms", "end_to_end_ms")}
        self._done_times: Deque[float] = deque(maxlen=50)

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"live-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._release_tracks()
        self.state = "stopped"

    # -- main loop -------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            if self.cfg.detect and self.tracker_factory is not None:
                self.state = "loading_model"
                self.tracker = self.tracker_factory()
                if hasattr(self.tracker, "warmup"):
                    self.tracker.warmup()
            self.state = "running"
        except Exception as e:
            self.state, self.last_error = "error", f"model load failed: {e}"
            logger.error("camera %s: %s", self.camera_id, self.last_error, exc_info=True)
            return
        last_seq = 0
        t_next = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now < t_next:
                self._stop.wait(t_next - now)
                continue
            behind = now - t_next
            if behind > self.period:          # overran: skip the slots we missed, never queue them
                skipped = int(behind / self.period)
                self.missed_slots += skipped
                t_next += skipped * self.period
            t_next += self.period
            item = self.source.latest(newer_than=last_seq, timeout=self.period)
            if item is None:
                continue
            frame, captured_at, last_seq = item
            try:
                self._process(frame, captured_at, last_seq)
                self.last_error = None
            except Exception as e:  # one bad frame must not kill the camera
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning("camera %s frame %s: %s", self.camera_id, last_seq, e, exc_info=True)

    def _process(self, frame, captured_at: datetime, seq: int) -> None:
        h, w = frame.shape[:2]
        dets: List[Dict] = []
        t0 = time.perf_counter()
        if self.tracker is not None:
            for d in self.tracker.update(frame):
                b = d.bbox
                dets.append({"id": d.local_track_id, "conf": round(float(d.confidence), 3),
                             "box": [float(b.u_min), float(b.v_min), float(b.u_max - b.u_min), float(b.v_max - b.v_min)]})
        t1 = time.perf_counter()
        incidents: List[Any] = []
        if self.cfg.feed_twin and self.tracker is not None:
            ts = canonical_utc(captured_at)
            with TWIN_WRITE_LOCK:
                for d in dets:
                    p = self.projector.project(self.camera_id, d["box"], (w, h)) if self.projector else None
                    if p is None:
                        continue
                    d["floor"] = [round(p[0], 2), round(p[1], 2), p[2]]
                    tid = f"{self.track_prefix}{d['id']}"
                    if self.entry_gate is not None:
                        self.entry_gate.observe(self.camera_id, tid, d["box"], (w, h))
                    evs = self.twin.ingest_observation(tid, self.camera_id,
                                                       p[0], p[1], ts, d["conf"])
                    if self.rules is not None and evs:
                        incidents += self.rules.evaluate(evs)
                lost = self.twin.prune_tracks(now_iso=ts, ttl_seconds=self.track_exit_seconds,
                                              track_id_prefix=self.track_prefix)
                if lost and self.rules is not None:
                    incidents += self.rules.evaluate(lost)
        t2 = time.perf_counter()
        with self._lock:
            self.last_frame, self.last_frame_seq, self.last_captured_at = frame, seq, captured_at
            self.last_detections = dets
        self.people_in_view = len(dets)
        self.frames_processed += 1
        self._lat["detect_ms"].append((t1 - t0) * 1000)
        self._lat["twin_ms"].append((t2 - t1) * 1000)
        self._lat["end_to_end_ms"].append((datetime.now(timezone.utc) - captured_at).total_seconds() * 1000)
        self._done_times.append(time.monotonic())
        if incidents:
            self.incidents_raised += len(incidents)
            if self.on_incidents:
                try:
                    self.on_incidents(incidents)
                except Exception:
                    logger.warning("incident callback failed", exc_info=True)

    def _release_tracks(self) -> None:
        """On stop, everyone this camera was tracking leaves the twin (TRACK_LOST, exits)."""
        if self.twin is None or not self.cfg.feed_twin:
            return
        ref = self.last_captured_at or datetime.now(timezone.utc)
        after_exit = canonical_utc(ref + timedelta(seconds=self.track_exit_seconds + 1))
        try:
            with TWIN_WRITE_LOCK:
                lost = self.twin.prune_tracks(now_iso=after_exit, ttl_seconds=self.track_exit_seconds,
                                              track_id_prefix=self.track_prefix)
                if lost and self.rules is not None:
                    self.rules.evaluate(lost)
        except Exception:
            logger.warning("camera %s: releasing tracks failed", self.camera_id, exc_info=True)

    # -- views -----------------------------------------------------------------------------
    def snapshot(self):
        with self._lock:
            return self.last_frame, list(self.last_detections), self.last_frame_seq, self.last_captured_at

    def status(self) -> Dict[str, Any]:
        lat = {k: {"p50": _pct(v, 0.5), "p95": _pct(v, 0.95)} for k, v in self._lat.items()}
        dt = self._done_times
        fps = round((len(dt) - 1) / (dt[-1] - dt[0]), 2) if len(dt) >= 2 and dt[-1] > dt[0] else 0.0
        return {"camera_id": self.camera_id, "state": self.state, "feed_twin": self.cfg.feed_twin,
                "detect": self.cfg.detect, "device": self.cfg.device, "model": self.cfg.model,
                "target_fps": round(1 / self.period, 2), "processed_fps": fps,
                "frames_processed": self.frames_processed, "missed_slots": self.missed_slots,
                "people_in_view": self.people_in_view, "incidents_raised": self.incidents_raised,
                "latency_ms": lat, "last_error": self.last_error, "source": self.source.status()}
