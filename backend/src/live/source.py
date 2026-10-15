"""A camera stream reader that always holds the NEWEST frame.

Live video must never queue: if detection is slower than the camera, old frames are dropped and
the worker always gets the most recent picture. Each frame is stamped with the wall-clock UTC time
at which it was read. Lost streams are reopened with exponential backoff.
"""
from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
from typing import Any, Optional, Tuple

logger = logging.getLogger("office_vision.live.source")


class FrameSource:
    def __init__(self, camera_id: str, url: str, realtime: bool = True, loop: bool = True,
                 max_backoff_s: float = 10.0, open_fn=None):
        self.camera_id = camera_id
        self.url = url
        self.is_file = "://" not in url
        self.realtime = realtime
        self.loop = loop
        self.max_backoff_s = max_backoff_s
        self._open_fn = open_fn  # tests inject a fake capture factory
        self._cond = threading.Condition()
        self._frame: Any = None
        self._captured_at: Optional[datetime] = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state = "stopped"  # connecting / live / reconnecting / ended / stopped
        self.reconnects = 0
        self.frames_read = 0
        self.source_fps = 0.0
        self.last_error: Optional[str] = None
        self.size: Optional[Tuple[int, int]] = None  # (width, height)

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"src-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout)
        self.state = "stopped"

    # -- consumer API ----------------------------------------------------------------------
    def latest(self, newer_than: int = 0, timeout: float = 0.0):
        """(frame, captured_at, seq) for the newest frame with seq > newer_than, else None."""
        with self._cond:
            if self._seq <= newer_than and timeout > 0 and not self._stop.is_set():
                self._cond.wait(timeout)
            if self._seq <= newer_than or self._frame is None:
                return None
            return self._frame, self._captured_at, self._seq

    # -- reader thread ---------------------------------------------------------------------
    def _open(self):
        if self._open_fn is not None:
            return self._open_fn(self.url)
        import cv2
        if self.is_file and not Path(self.url).exists():
            raise FileNotFoundError(self.url)
        cap = cv2.VideoCapture(self.url)
        try:  # network streams: keep the driver's own buffer minimal so we see the newest frame
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            self.state = "connecting" if self.reconnects == 0 and self.frames_read == 0 else "reconnecting"
            try:
                cap = self._open()
                if cap is None or not cap.isOpened():
                    raise ConnectionError(f"cannot open {_redact(self.url)}")
            except Exception as e:
                self.last_error = str(e)
                logger.warning("camera %s: %s (retry in %.1fs)", self.camera_id, e, backoff)
                self._stop.wait(backoff)
                backoff = min(self.max_backoff_s, backoff * 2)
                self.reconnects += 1
                continue
            try:
                ended = self._read_loop(cap)
            finally:
                cap.release()
            if self._stop.is_set():
                break
            if ended and not self.loop:
                self.state = "ended"
                return
            if ended:
                backoff = 0.5
                continue  # file replay: start again from the beginning
            self.reconnects += 1
            self.state = "reconnecting"
            self._stop.wait(backoff)
            backoff = min(self.max_backoff_s, backoff * 2)
        self.state = "stopped"

    def _read_loop(self, cap) -> bool:
        """Read until stop or failure. True when a file reached its end, False on a stream error."""
        fps = 0.0
        try:
            import cv2
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        except Exception:
            pass
        period = 1.0 / fps if (self.is_file and self.realtime and 0 < fps <= 120) else 0.0
        t_next = time.monotonic()
        window_t, window_n = time.monotonic(), 0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                if self.is_file:
                    return True
                self.last_error = "stream read failed"
                return False
            now = datetime.now(timezone.utc)
            with self._cond:
                self._frame, self._captured_at = frame, now
                self._seq += 1
                self._cond.notify_all()
            self.frames_read += 1
            self.state = "live"
            self.size = (int(frame.shape[1]), int(frame.shape[0]))
            window_n += 1
            if time.monotonic() - window_t >= 2.0:
                self.source_fps = round(window_n / (time.monotonic() - window_t), 1)
                window_t, window_n = time.monotonic(), 0
            if period:
                t_next += period
                delay = t_next - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    t_next = time.monotonic()
        return False

    def status(self) -> dict:
        return {"state": self.state, "url": _redact(self.url), "frames_read": self.frames_read,
                "source_fps": self.source_fps, "reconnects": self.reconnects,
                "size": list(self.size) if self.size else None, "last_error": self.last_error}


def _redact(url: str) -> str:
    """Hide credentials in rtsp://user:pass@host URLs."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.rsplit('@', 1)[1]}"
    return url
