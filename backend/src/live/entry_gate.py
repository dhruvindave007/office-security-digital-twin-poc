# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Decides whether a camera track entering a badge-controlled room is a REAL door entry.

Problem it solves (measured on WiseNET): a tracker that loses someone inside the room and finds them
again creates a new track that "enters" the room in the middle of it. Without a gate each such
re-acquisition becomes a false ACCESS_WITHOUT_BADGE / TAILGATING alarm.

Two checks, both using only what has been observed so far (valid live):
  at entry time   (entry_filter):  the track's FIRST box is at the door region of that camera.
  at decision time (entry_confirm): the track was seen at least `min_sightings` times by then
                                    (2-3 sighting blips are the tracker losing and re-finding someone).
Cameras without a configured door region are not gated.
"""
import threading
from typing import Dict, List, Optional, Tuple


class EntryGate:
    def __init__(self, doors: Dict[str, Dict]):
        """doors: {camera_id: {"roi": [x, y, w, h], "image_size": [w, h], "margin_frac": 0.33, "min_sightings": 5}}"""
        self.doors = doors
        self._first: Dict[Tuple[str, str], Tuple[List[float], Tuple[int, int]]] = {}
        self._count: Dict[Tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def observe(self, camera_id: str, track_id: str, box_xywh: List[float], img_wh: Tuple[int, int]) -> None:
        if camera_id not in self.doors:
            return
        key = (camera_id, track_id)
        with self._lock:
            self._first.setdefault(key, (list(box_xywh), img_wh))
            self._count[key] = self._count.get(key, 0) + 1

    def forget(self, camera_id: str, track_id: str) -> None:
        with self._lock:
            self._first.pop((camera_id, track_id), None)
            self._count.pop((camera_id, track_id), None)

    def entry_filter(self, event) -> bool:
        door = self.doors.get(event.camera_id)
        if door is None:
            return True
        with self._lock:
            first = self._first.get((event.camera_id, event.track_id))
        if first is None:
            return False
        (x, y, w, h), (iw, ih) = first
        sx, sy = door["image_size"][0] / iw, door["image_size"][1] / ih
        x, y, w, h = x * sx, y * sy, w * sx, h * sy
        rx, ry, rw, rh = door["roi"]
        m = rw * door.get("margin_frac", 0.33)
        return not (x + w < rx - m or x > rx + rw + m or y + h < ry or y > ry + rh + m)

    def entry_confirm(self, event) -> bool:
        door = self.doors.get(event.camera_id)
        if door is None:
            return True
        with self._lock:
            n = self._count.get((event.camera_id, event.track_id), 0)
        return n >= door.get("min_sightings", 5)
