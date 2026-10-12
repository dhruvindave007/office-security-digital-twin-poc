from collections import deque
from datetime import datetime
import threading
import time
from typing import Any, Dict


class TwinTelemetry:
    """Thread-safe operational telemetry metrics collector for the Digital Twin."""

    def __init__(self, rate_window_seconds: float = 60.0):
        self.rate_window_seconds = rate_window_seconds
        self._lock = threading.Lock()
        self._total_events_ingested: int = 0
        self._snapshot_count: int = 0
        self._last_ingest_latency_ms: float = 0.0
        self._event_timestamps: deque = deque()

    def record_ingest(self, event_count: int, latency_ms: float) -> None:
        """Record an ingestion operation with latency and event count."""
        now = time.time()
        with self._lock:
            self._total_events_ingested += event_count
            self._last_ingest_latency_ms = round(latency_ms, 2)
            for _ in range(event_count):
                self._event_timestamps.append(now)

            # Evict timestamps older than rate_window_seconds
            cutoff = now - self.rate_window_seconds
            while self._event_timestamps and self._event_timestamps[0] < cutoff:
                self._event_timestamps.popleft()

    def record_snapshot(self) -> None:
        """Increment snapshot creation counter."""
        with self._lock:
            self._snapshot_count += 1

    def get_metrics(
        self,
        active_tracks_count: int = 0,
        zone_occupancies: Dict[str, int] = None,
    ) -> Dict[str, Any]:
        """Return a point-in-time snapshot of telemetry metrics."""
        now = time.time()
        with self._lock:
            cutoff = now - self.rate_window_seconds
            while self._event_timestamps and self._event_timestamps[0] < cutoff:
                self._event_timestamps.popleft()

            window_duration = max(1.0, min(self.rate_window_seconds, now - self._event_timestamps[0])) if self._event_timestamps else 1.0
            event_rate = round(len(self._event_timestamps) / window_duration, 2)

            return {
                "total_events_ingested": self._total_events_ingested,
                "snapshot_count": self._snapshot_count,
                "last_ingest_latency_ms": self._last_ingest_latency_ms,
                "event_rate_per_sec": event_rate,
                "active_tracks_count": active_tracks_count,
                "zone_occupancies": zone_occupancies or {},
                "timestamp": datetime.utcnow().isoformat(),
            }
