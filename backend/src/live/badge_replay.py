# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Demo helper: replays badge swipes in step with a looping recorded video.

Recordings have no badge reader, so the demo plays scripted swipes at fixed offsets after each loop
of one camera's video starts (e.g. "badge_1003 GRANTED at door_d4, 20.2 s in"), through the same
access-control service a real reader would call. Swipes are stamped with the wall clock.
"""
from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Dict, List, Optional

try:
    from src.core.timeutil import canonical_utc
except ImportError:
    from core.timeutil import canonical_utc

logger = logging.getLogger("office_vision.live.badge_replay")


class BadgeReplayer:
    def __init__(self, source, access_service, swipes: List[Dict[str, Any]]):
        self.source = source
        self.access = access_service
        self.swipes = sorted(swipes, key=lambda s: float(s["offset_s"]))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.fired = 0
        self.last_error: Optional[str] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="badge-replay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)

    def _run(self) -> None:
        loop_seen, next_i = 0, 0
        while not self._stop.wait(0.05):
            start, loops = self.source.loop_started_at, self.source.loops
            if start is None:
                continue
            if loops != loop_seen:                 # a new pass through the video began
                loop_seen, next_i = loops, 0
            now = datetime.now(timezone.utc)
            while next_i < len(self.swipes) and now >= start + timedelta(seconds=float(self.swipes[next_i]["offset_s"])):
                s = self.swipes[next_i]
                at = start + timedelta(seconds=float(s["offset_s"]))
                next_i += 1
                if (now - at).total_seconds() > 2.0:   # far behind (e.g. started mid-loop): skip, don't fake times
                    continue
                try:
                    self.access.ingest(s["door_id"], s["badge_id"], s.get("result", "GRANTED"), canonical_utc(at))
                    self.fired += 1
                except Exception as e:
                    self.last_error = str(e)
                    logger.warning("badge replay failed: %s", e)
