from collections import defaultdict
from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from src.twin.models import TrackedEntity


class TwinState:
    """Thread-safe in-memory entity registry and occupancy cache for the Digital Twin.

    Guarantees O(1) track lookup, O(1) zone occupancy resolution, and thread-safe
    mutations guarded by an internal reentrant/standard threading.Lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._active_tracks: Dict[str, TrackedEntity] = {}
        self._zone_occupancy: Dict[str, Set[str]] = defaultdict(set)

    def update_track(
        self,
        track_id: str,
        camera_id: Optional[str],
        zone_id: Optional[str],
        position: Optional[Tuple[float, float]],
        timestamp: str,
        confidence: float = 1.0,
        person_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Update or register an entity track and maintain zone occupancy sets.

        Returns:
            Tuple[old_zone_id, new_zone_id] representing spatial delta.
        """
        with self._lock:
            old_zone: Optional[str] = None
            if track_id in self._active_tracks:
                entity = self._active_tracks[track_id]
                old_zone = entity.current_zone_id
                entity.last_seen = timestamp
                entity.position = position
                entity.confidence = confidence
                entity.status = "ACTIVE"
                if camera_id:
                    entity.current_camera_id = camera_id
                if person_id and not entity.person_id:
                    entity.person_id = person_id

                if old_zone != zone_id:
                    if old_zone and old_zone in self._zone_occupancy:
                        self._zone_occupancy[old_zone].discard(track_id)
                    if zone_id:
                        self._zone_occupancy[zone_id].add(track_id)
                        entity.dwell_start = timestamp
                    else:
                        entity.dwell_start = None
                    entity.current_zone_id = zone_id
            else:
                entity = TrackedEntity(
                    track_id=track_id,
                    person_id=person_id,
                    current_zone_id=zone_id,
                    current_camera_id=camera_id,
                    position=position,
                    first_seen=timestamp,
                    last_seen=timestamp,
                    dwell_start=timestamp if zone_id else None,
                    confidence=confidence,
                    status="ACTIVE",
                )
                self._active_tracks[track_id] = entity
                if zone_id:
                    self._zone_occupancy[zone_id].add(track_id)

            return old_zone, zone_id

    def assign_person(self, track_id: str, person_id: str) -> bool:
        """Attach an identity learned from another source (e.g. the badge that admitted this track)."""
        with self._lock:
            entity = self._active_tracks.get(track_id)
            if entity is None or entity.person_id:
                return False
            entity.person_id = person_id
            return True

    def remove_track(self, track_id: str) -> Optional[TrackedEntity]:
        """Remove a track (e.g. replaying TRACK_LOST) and release its zone occupancy."""
        with self._lock:
            entity = self._active_tracks.pop(track_id, None)
            if entity and entity.current_zone_id and entity.current_zone_id in self._zone_occupancy:
                self._zone_occupancy[entity.current_zone_id].discard(track_id)
            return entity

    def get_track(self, track_id: str) -> Optional[TrackedEntity]:
        """Fetch a copy of a tracked entity by ID."""
        with self._lock:
            entity = self._active_tracks.get(track_id)
            if not entity:
                return None
            return TrackedEntity(
                track_id=entity.track_id,
                person_id=entity.person_id,
                current_zone_id=entity.current_zone_id,
                current_camera_id=entity.current_camera_id,
                position=entity.position,
                first_seen=entity.first_seen,
                last_seen=entity.last_seen,
                dwell_start=entity.dwell_start,
                confidence=entity.confidence,
                status=entity.status,
            )

    def get_active_tracks(self) -> List[TrackedEntity]:
        """Fetch all currently active tracked entities."""
        with self._lock:
            return [
                TrackedEntity(
                    track_id=e.track_id,
                    person_id=e.person_id,
                    current_zone_id=e.current_zone_id,
                    current_camera_id=e.current_camera_id,
                    position=e.position,
                    first_seen=e.first_seen,
                    last_seen=e.last_seen,
                    dwell_start=e.dwell_start,
                    confidence=e.confidence,
                    status=e.status,
                )
                for e in self._active_tracks.values()
            ]

    def get_zone_occupancy(self, zone_id: str) -> int:
        """Fetch current occupancy count for a given zone."""
        with self._lock:
            return len(self._zone_occupancy.get(zone_id, set()))

    def get_all_zone_occupancies(self) -> Dict[str, int]:
        """Fetch occupancy counts for all tracked zones."""
        with self._lock:
            return {zone_id: len(tracks) for zone_id, tracks in self._zone_occupancy.items()}

    def prune_inactive_tracks(
        self, now_iso: str, ttl_seconds: float, track_id_prefix: Optional[str] = None
    ) -> List[TrackedEntity]:
        """Evict entities not seen within ttl_seconds and return pruned records.

        If track_id_prefix is given, only tracks whose ID starts with it are considered
        (used by clip runs, which must never evict tracks they do not own).
        """
        now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        pruned: List[TrackedEntity] = []

        with self._lock:
            inactive_ids = []
            for track_id, entity in self._active_tracks.items():
                if track_id_prefix is not None and not track_id.startswith(track_id_prefix):
                    continue
                last_seen_dt = datetime.fromisoformat(entity.last_seen.replace("Z", "+00:00"))
                if last_seen_dt.tzinfo is None:
                    last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
                elapsed = (now_dt - last_seen_dt).total_seconds()
                if elapsed > ttl_seconds:
                    inactive_ids.append(track_id)
                    entity.status = "LOST"
                    pruned.append(entity)

            for tid in inactive_ids:
                entity = self._active_tracks.pop(tid)
                if entity.current_zone_id and entity.current_zone_id in self._zone_occupancy:
                    self._zone_occupancy[entity.current_zone_id].discard(tid)

        return pruned

    def to_dict(self) -> Dict[str, Any]:
        """Serialize current state into a JSON-compatible dictionary."""
        with self._lock:
            return {
                "active_tracks": [
                    {
                        "track_id": e.track_id,
                        "person_id": e.person_id,
                        "current_zone_id": e.current_zone_id,
                        "current_camera_id": e.current_camera_id,
                        "position": list(e.position) if e.position else None,
                        "first_seen": e.first_seen,
                        "last_seen": e.last_seen,
                        "dwell_start": e.dwell_start,
                        "confidence": e.confidence,
                        "status": e.status,
                    }
                    for e in self._active_tracks.values()
                ],
                "zone_occupancy": {k: list(v) for k, v in self._zone_occupancy.items()},
            }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Restore in-memory state from a serialized snapshot dictionary."""
        with self._lock:
            self._active_tracks.clear()
            self._zone_occupancy.clear()

            for item in data.get("active_tracks", []):
                pos = tuple(item["position"]) if item.get("position") else None
                entity = TrackedEntity(
                    track_id=item["track_id"],
                    person_id=item.get("person_id"),
                    current_zone_id=item.get("current_zone_id"),
                    current_camera_id=item.get("current_camera_id"),
                    position=pos,
                    first_seen=item.get("first_seen", ""),
                    last_seen=item.get("last_seen", ""),
                    dwell_start=item.get("dwell_start"),
                    confidence=item.get("confidence", 1.0),
                    status=item.get("status", "ACTIVE"),
                )
                self._active_tracks[entity.track_id] = entity

            for zone_id, track_ids in data.get("zone_occupancy", {}).items():
                self._zone_occupancy[zone_id] = set(track_ids)

    def clear(self) -> None:
        """Clear all tracks and occupancy caches."""
        with self._lock:
            self._active_tracks.clear()
            self._zone_occupancy.clear()
