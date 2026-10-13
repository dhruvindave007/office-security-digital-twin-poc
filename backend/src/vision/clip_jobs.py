from datetime import datetime, timezone
import logging
import threading
from typing import Any, Callable, Dict, Optional
import uuid

try:
    from src.vision.clip_runner import VideoClipRunner
    from src.vision.clips import ClipConfig
    from src.vision.tracking.base import PersonTracker
except ImportError:
    from vision.clip_runner import VideoClipRunner
    from vision.clips import ClipConfig
    from vision.tracking.base import PersonTracker

logger = logging.getLogger("office_vision.vision.clip_jobs")


class JobConflictError(Exception):
    """Raised when an operation conflicts with the current job state (e.g., run already active or already finished)."""
    pass


class JobNotFoundError(Exception):
    """Raised when a requested run_id is not found in the manager registry."""
    pass


class ClipJobManager:
    """Manages asynchronous video clip evaluation jobs in background threads."""

    def __init__(self, runner: VideoClipRunner):
        self.runner = runner
        self._lock = threading.Lock()
        self._active_run_id: Optional[str] = None
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._threads: Dict[str, threading.Thread] = {}

    @property
    def active_run_id(self) -> Optional[str]:
        with self._lock:
            return self._active_run_id

    def start(self, config: ClipConfig, tracker_factory: Callable[[], PersonTracker]) -> str:
        """Start a clip evaluation run in a background daemon thread.

        Enforces at most ONE active run at a time.
        """
        with self._lock:
            if self._active_run_id is not None:
                active_entry = self._runs.get(self._active_run_id)
                if active_entry and active_entry["status"] in ("queued", "running"):
                    raise JobConflictError(
                        f"Another clip run '{self._active_run_id}' is already active."
                    )

            # Random (not time-based) so concurrent/consecutive runs never share a prefix.
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            now_iso = datetime.now(timezone.utc).isoformat()
            cancel_event = threading.Event()
            self._cancel_events[run_id] = cancel_event
            self._runs[run_id] = {
                "run_id": run_id,
                "clip_name": config.name,
                "status": "queued",
                "frames_read": 0,
                "started_at": now_iso,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._active_run_id = run_id

        def _worker():
            def progress_callback(frames_read: int):
                with self._lock:
                    if run_id in self._runs:
                        self._runs[run_id]["frames_read"] = frames_read

            try:
                with self._lock:
                    self._runs[run_id]["status"] = "running"

                tracker = tracker_factory()
                result = self.runner.run(
                    config=config,
                    tracker=tracker,
                    run_id=run_id,
                    cancel_event=cancel_event,
                    progress_cb=progress_callback,
                )

                with self._lock:
                    now_fin = datetime.now(timezone.utc).isoformat()
                    self._runs[run_id]["finished_at"] = now_fin
                    self._runs[run_id]["frames_read"] = result.frames_read
                    self._runs[run_id]["result"] = result.to_dict()
                    if result.status == "cancelled":
                        self._runs[run_id]["status"] = "cancelled"
                    elif result.status == "failed":
                        self._runs[run_id]["status"] = "failed"
                        self._runs[run_id]["error"] = (
                            "; ".join(result.errors) if result.errors else "Run failed"
                        )
                    else:
                        self._runs[run_id]["status"] = "completed"
            except Exception as exc:
                logger.exception("Clip evaluation thread for run '%s' encountered error: %s", run_id, exc)
                with self._lock:
                    now_fin = datetime.now(timezone.utc).isoformat()
                    self._runs[run_id]["status"] = "failed"
                    self._runs[run_id]["finished_at"] = now_fin
                    self._runs[run_id]["error"] = str(exc)
            finally:
                with self._lock:
                    if self._active_run_id == run_id:
                        self._active_run_id = None

        thread = threading.Thread(
            target=_worker,
            name=f"clip-job-{run_id}",
            daemon=True,
        )
        self._threads[run_id] = thread
        thread.start()
        return run_id

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve job execution record by run_id."""
        with self._lock:
            entry = self._runs.get(run_id)
            return dict(entry) if entry else None

    def cancel(self, run_id: str) -> None:
        """Cancel an ongoing clip evaluation run.

        Raises:
            JobNotFoundError: If run_id does not exist.
            JobConflictError: If run is already completed, failed, or cancelled.
        """
        with self._lock:
            if run_id not in self._runs:
                raise JobNotFoundError(f"Job run '{run_id}' not found.")

            status = self._runs[run_id]["status"]
            if status in ("completed", "failed", "cancelled"):
                raise JobConflictError(
                    f"Job run '{run_id}' is already finished with status '{status}'."
                )

            cancel_event = self._cancel_events.get(run_id)
            if cancel_event:
                cancel_event.set()

    def get_thread(self, run_id: str) -> Optional[threading.Thread]:
        """Return the background worker thread for tests to join on."""
        with self._lock:
            return self._threads.get(run_id)
