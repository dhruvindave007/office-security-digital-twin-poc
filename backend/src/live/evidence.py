"""Rolling evidence recording: every camera keeps its last few seconds of processed frames in memory;
when a serious incident is raised, the frames before it (pre-roll) and after it (post-roll) are
saved as an H.264 clip plus key frames.

Bursts share clips: a new incident on a camera that is already recording joins that recording and
extends it (up to `max_clip_seconds`) instead of starting an overlapping copy.

Layout (data/evidence/):
  clips/<camera>_<start>.mp4            the video (boxes drawn; the incident's own track in red)
  clips/<camera>_<start>_key<k>.jpg     key frames (incident moment, first, last)
  incidents/<incident_id>.json          which clips cover this incident and at what offset
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import queue
import shutil
import subprocess
import threading
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("office_vision.live.evidence")
SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
VIDEO_FPS = 10.0      # evidence video timeline rate (frames are held between real captures)
_COLORS = [(66, 135, 245), (12, 83, 201), (82, 196, 103), (18, 156, 226), (180, 90, 200), (60, 60, 230), (200, 170, 40)]


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@dataclass
class _Frame:
    t: datetime
    jpeg: bytes
    dets: List[Dict]


@dataclass
class _Capture:
    camera_id: str
    start: datetime
    end: datetime
    hard_end: datetime
    frames: List[_Frame]
    incidents: Dict[str, Dict] = field(default_factory=dict)  # incident_id -> {time, track_local_id}


class EvidenceRecorder:
    def __init__(self, root: str, pre_seconds: float = 12.0, post_seconds: float = 8.0, max_clip_seconds: float = 60.0,
                 min_severity: str = "HIGH", max_total_mb: float = 2000.0, fps: float = 5.0,
                 camera_for_incident: Optional[Callable[[Any], List[str]]] = None, decision_delay_seconds: float = 8.0):
        self.root = Path(root)
        self.pre = timedelta(seconds=pre_seconds)
        self.post = timedelta(seconds=post_seconds)
        # Incidents can be raised a few seconds after the moment they describe (entries are decided at
        # the late-badge deadline), so memory must reach further back than the pre-roll itself.
        self.horizon = self.pre + timedelta(seconds=decision_delay_seconds)
        self.max_clip = timedelta(seconds=max_clip_seconds)
        self.min_rank = SEVERITY_RANK.get(min_severity, 2)
        self.max_total_bytes = max_total_mb * 2**20
        self.fps = fps
        self.camera_for_incident = camera_for_incident or (lambda inc: [])
        self._buffers: Dict[str, Deque[_Frame]] = {}
        self._active: Dict[str, _Capture] = {}
        self._lock = threading.Lock()
        self._jobs: "queue.Queue[Optional[_Capture]]" = queue.Queue()
        self._writer = threading.Thread(target=self._write_loop, name="evidence-writer", daemon=True)
        self._writer.start()
        self.clips_written = 0
        self.last_error: Optional[str] = None
        self._ready: Dict[str, threading.Event] = {}

    # -- frames in (camera workers) --------------------------------------------------------
    def add_frame(self, camera_id: str, captured_at: datetime, frame, dets: List[Dict]) -> None:
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return
        f = _Frame(captured_at, buf.tobytes(), dets)
        done: Optional[_Capture] = None
        with self._lock:
            b = self._buffers.setdefault(camera_id, deque())
            b.append(f)
            while b and captured_at - b[0].t > self.horizon:
                b.popleft()
            cap = self._active.get(camera_id)
            if cap is not None:
                cap.frames.append(f)
                if captured_at >= cap.end:
                    done = self._active.pop(camera_id)
        if done is not None:
            self._jobs.put(done)

    # -- incidents in (rule engine listener; called inside the twin lock: only bookkeeping) -
    def on_incidents(self, new: List[Any], _changed: List[Any]) -> None:
        for inc in new:
            if SEVERITY_RANK.get(inc.severity, 0) < self.min_rank:
                continue
            t = _parse(inc.start_time)
            local = None
            if inc.track_id and inc.track_id.startswith("live:"):
                local = inc.track_id.rsplit(":", 1)[1]
            self._ready.setdefault(inc.incident_id, threading.Event())
            cams = self.camera_for_incident(inc)
            if not cams:
                self._ready[inc.incident_id].set()
            for cam in cams:
                with self._lock:
                    cap = self._active.get(cam)
                    if cap is None:
                        pre = [f for f in self._buffers.get(cam, ()) if f.t >= t - self.pre]
                        start = pre[0].t if pre else t
                        cap = _Capture(cam, start, t + self.post, start + self.max_clip, list(pre))
                        self._active[cam] = cap
                    else:
                        cap.end = min(max(cap.end, t + self.post), cap.hard_end)
                    cap.incidents[inc.incident_id] = {"time": inc.start_time, "track": local,
                                                      "type": inc.incident_type, "severity": inc.severity}

    def wait_ready(self, incident_id: str, timeout: float) -> bool:
        """Block until this incident's evidence is written (used by the auto-investigator)."""
        ev = self._ready.get(incident_id)
        return ev.wait(timeout) if ev is not None else False

    def flush(self) -> None:
        """Finish all recordings now (e.g. on shutdown)."""
        with self._lock:
            caps, self._active = list(self._active.values()), {}
        for c in caps:
            self._jobs.put(c)

    def close(self) -> None:
        self.flush()
        self._jobs.put(None)
        self._writer.join(30)

    # -- writing (background thread) -------------------------------------------------------
    def _write_loop(self) -> None:
        while True:
            cap = self._jobs.get()
            if cap is None:
                return
            try:
                self._write(cap)
                self.clips_written += 1
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error("evidence write failed for %s: %s", cap.camera_id, e, exc_info=True)
            finally:
                for iid in cap.incidents:
                    self._ready.setdefault(iid, threading.Event()).set()
            self._enforce_quota()

    def _write(self, cap: _Capture) -> None:
        import cv2
        import numpy as np
        if not cap.frames:
            return
        clips = self.root / "clips"
        incs = self.root / "incidents"
        clips.mkdir(parents=True, exist_ok=True)
        incs.mkdir(parents=True, exist_ok=True)
        stem = f"{cap.camera_id}_{cap.frames[0].t:%Y%m%dT%H%M%S}"
        red_tracks = {str(v["track"]) for v in cap.incidents.values() if v["track"] is not None}
        imgs = []
        for f in cap.frames:
            img = cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_COLOR)
            for d in f.dets:
                x, y, w, h = [int(v) for v in d["box"]]
                hit = str(d["id"]) in red_tracks
                col = (40, 40, 230) if hit else _COLORS[d["id"] % len(_COLORS)]
                cv2.rectangle(img, (x, y), (x + w, y + h), col, 3 if hit else 2)
                cv2.putText(img, f"#{d['id']}", (x + 3, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
            cv2.rectangle(img, (0, 0), (img.shape[1], 22), (20, 20, 20), -1)
            cv2.putText(img, f"{cap.camera_id}  {f.t:%Y-%m-%d %H:%M:%S.%f}"[:-3] + " UTC", (6, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            imgs.append(img)
        h, w = imgs[0].shape[:2]
        # Real time, not frame count: cameras deliver uneven frame rates (fewer under load), so the video
        # is laid out on a fixed VIDEO_FPS wall-clock timeline, each tick showing the newest real frame
        # (held through gaps, never invented). Video time then equals capture time to within 1/VIDEO_FPS,
        # so incident_offset_s seeks to the right moment.
        t0 = cap.frames[0].t
        span = (cap.frames[-1].t - t0).total_seconds()
        timeline, j = [], 0
        for k in range(int(round(span * VIDEO_FPS)) + 1):
            while j + 1 < len(cap.frames) and (cap.frames[j + 1].t - t0).total_seconds() <= k / VIDEO_FPS + 1e-6:
                j += 1
            timeline.append(j)
        video = clips / f"{stem}.mp4"
        if shutil.which("ffmpeg"):
            p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                                  "-s", f"{w}x{h}", "-r", str(VIDEO_FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                                  "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)],
                                 stdin=subprocess.PIPE)
            for i in timeline:
                p.stdin.write(cv2.resize(imgs[i], (w, h)).tobytes())
            p.stdin.close()
            if p.wait(120) != 0:
                raise RuntimeError("ffmpeg failed to encode evidence clip")
        else:  # no ffmpeg: Motion-JPEG AVI (bigger, but plays everywhere)
            video = clips / f"{stem}.avi"
            wr = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), VIDEO_FPS, (w, h))
            for i in timeline:
                wr.write(cv2.resize(imgs[i], (w, h)))
            wr.release()
        for iid, meta in cap.incidents.items():
            it = _parse(meta["time"])
            k = min(range(len(cap.frames)), key=lambda i: abs((cap.frames[i].t - it).total_seconds()))
            key = clips / f"{stem}_key{k}.jpg"
            cv2.imwrite(str(key), imgs[k], [cv2.IMWRITE_JPEG_QUALITY, 85])
            path = incs / f"{iid}.json"
            entry = {"camera_id": cap.camera_id, "video": f"clips/{video.name}", "key_frame": f"clips/{key.name}",
                     "clip_start": t0.isoformat(), "clip_end": cap.frames[-1].t.isoformat(), "frames": len(imgs), "video_fps": VIDEO_FPS,
                     "incident_offset_s": round((it - t0).total_seconds(), 2), "track": meta["track"]}
            doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"incident_id": iid, "clips": []}
            doc["clips"] = [c for c in doc["clips"] if c["video"] != entry["video"]] + [entry]
            path.write_text(json.dumps(doc, indent=1), encoding="utf-8")

    def _enforce_quota(self) -> None:
        clips = self.root / "clips"
        if not clips.exists():
            return
        files = sorted(clips.iterdir(), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        while files and total > self.max_total_bytes:
            old = files.pop(0)
            total -= old.stat().st_size
            old.unlink(missing_ok=True)

    # -- reads ---------------------------------------------------------------------------
    def manifest(self, incident_id: str) -> Optional[Dict]:
        path = self.root / "incidents" / f"{incident_id}.json"
        if not path.exists():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["clips"] = [c for c in doc["clips"] if (self.root / c["video"]).exists()]   # quota may have removed some
        return doc

    def status(self) -> Dict[str, Any]:
        with self._lock:
            active = {c: {"incidents": len(cap.incidents), "frames": len(cap.frames)} for c, cap in self._active.items()}
            buffered = {c: len(b) for c, b in self._buffers.items()}
        return {"clips_written": self.clips_written, "recording": active, "buffered_frames": buffered,
                "pending_writes": self._jobs.qsize(), "last_error": self.last_error}
