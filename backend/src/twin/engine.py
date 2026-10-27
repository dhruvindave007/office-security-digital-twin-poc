# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from src.twin.geometry import GeometryEngine
from src.twin.models import TrackedEntity, TwinEvent
from src.twin.snapshot import SnapshotManager
from src.twin.state import TwinState
from src.twin.store import TwinStore
from src.twin.telemetry import TwinTelemetry
from src.twin.validators import EventValidator
from src.core.timeutil import canonical_utc, parse_utc, utc_now_canonical

logger = logging.getLogger("office_vision.twin.engine")


def generate_uuid7() -> str:
    """Generate an RFC 9562 UUIDv7 string with millisecond time ordering."""
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)
    version_and_rand_a = 0x7000 | (int.from_bytes(rand_bytes[:2], "big") & 0x0FFF)
    rand_b_high = (rand_bytes[2] & 0x3F) | 0x80
    byte_array = (
        timestamp_ms.to_bytes(6, "big")
        + version_and_rand_a.to_bytes(2, "big")
        + bytes([rand_b_high])
        + rand_bytes[3:10]
    )
    return str(uuid.UUID(bytes=byte_array))


class DigitalTwinEngine:
    """Deterministic Digital Twin state machine orchestrating state, geometry, and the event ledger.

    Converts raw spatial observations into discrete domain events, updates the in-memory
    entity registry, manages periodic snapshot checkpoints, and supports 100% deterministic replay.
    """

    def __init__(
        self,
        store: TwinStore,
        state: Optional[TwinState] = None,
        geometry: Optional[GeometryEngine] = None,
        snapshot_manager: Optional[SnapshotManager] = None,
        telemetry: Optional[TwinTelemetry] = None,
        dwell_threshold_seconds: float = 300.0,
        track_ttl_seconds: float = 30.0,
        position_sample_seconds: float = 0.0,
    ):
        self.store = store
        # Store a PERSON_DETECTED position at most this often per track (0 = every detection). Zone
        # changes are always stored; skipped samples stay in memory and are written if a rule cites one.
        # Live 6 cameras x 5 fps wrote ~1.6 GB/day at every detection.
        self.position_sample_seconds = position_sample_seconds
        self._last_pos_stored: Dict[str, Any] = {}
        self.state = state or TwinState()
        self.snapshot_manager = snapshot_manager or SnapshotManager()
        self.telemetry = telemetry or TwinTelemetry()
        self.dwell_threshold_seconds = dwell_threshold_seconds
        self.track_ttl_seconds = track_ttl_seconds
        # (track_id, zone_id, dwell_start) already alerted: DWELL_TIME_EXCEEDED fires once per dwell period.
        self._dwell_alerted: set = set()

        if geometry:
            self.geometry = geometry
        else:
            self.reload_geometry()

    def reload_geometry(self) -> None:
        """Load zone boundaries from store and initialize the STRtree spatial index."""
        zones = self.store.get_zones()
        vertices_by_zone = {}
        for z in zones:
            vertices = self.store.get_zone_vertices(z.zone_id)
            vertices_by_zone[z.zone_id] = [(v.x, v.y) for v in vertices]

        self.geometry = GeometryEngine(zones, vertices_by_zone)
        logger.info(
            "Initialized GeometryEngine with %d zones (geometry_version=%d).",
            len(zones),
            self.geometry.geometry_version,
        )

    def ingest_observation(
        self,
        track_id: str,
        camera_id: Optional[str],
        x: float,
        y: float,
        timestamp: Optional[str] = None,
        confidence: float = 1.0,
        person_id: Optional[str] = None,
    ) -> List[TwinEvent]:
        """Ingest a 2D planar observation, evaluate transitions, and persist ledger events.

        Returns:
            List[TwinEvent] generated during this observation cycle.
        """
        start_time = time.perf_counter()

        # 1. Validation
        track_id = EventValidator.validate_track_id(track_id)
        x, y = EventValidator.validate_coordinates((x, y))
        confidence = EventValidator.validate_confidence(confidence)
        if not timestamp:
            timestamp = utc_now_canonical()
        else:
            timestamp = EventValidator.validate_timestamp(timestamp)

        # 2. Check existing entity before update
        existing_entity = self.state.get_track(track_id)
        is_first_detection = existing_entity is None

        # 3. Spatial zone resolution via STRtree + PreparedGeometry
        new_zone_id = self.geometry.find_zone(x, y)

        # 4. Mutate in-memory state
        old_zone_id, _ = self.state.update_track(
            track_id=track_id,
            camera_id=camera_id,
            zone_id=new_zone_id,
            position=(x, y),
            timestamp=timestamp,
            confidence=confidence,
            person_id=person_id,
        )

        # The twin may already know who this track is (e.g. from an earlier observation);
        # stamp events with that identity so rules never see a known person as "unknown".
        tracked_now = self.state.get_track(track_id)
        if person_id is None and tracked_now is not None:
            person_id = tracked_now.person_id

        # 5. Generate discrete domain events
        emitted_events: List[TwinEvent] = []
        coord_json = json.dumps({"x": round(x, 3), "y": round(y, 3)})

        # A: Observation detection event
        emitted_events.append(
            TwinEvent(
                event_id=generate_uuid7(),
                event_type="PERSON_DETECTED",
                timestamp=timestamp,
                track_id=track_id,
                person_id=person_id,
                camera_id=camera_id,
                zone_id=new_zone_id,
                previous_zone_id=old_zone_id,
                confidence=confidence,
                coordinates_json=coord_json,
            )
        )

        # B: Zone transitions
        if is_first_detection and new_zone_id is not None:
            # Immediate entry upon first detection
            emitted_events.append(
                TwinEvent(
                    event_id=generate_uuid7(),
                    event_type="ZONE_ENTERED",
                    timestamp=timestamp,
                    track_id=track_id,
                    person_id=person_id,
                    camera_id=camera_id,
                    zone_id=new_zone_id,
                    previous_zone_id=None,
                    confidence=confidence,
                    coordinates_json=coord_json,
                )
            )
        elif not is_first_detection and old_zone_id != new_zone_id:
            if old_zone_id is not None and new_zone_id is None:
                # Exited a zone into unzoned territory
                emitted_events.append(
                    TwinEvent(
                        event_id=generate_uuid7(),
                        event_type="ZONE_EXITED",
                        timestamp=timestamp,
                        track_id=track_id,
                        person_id=person_id,
                        camera_id=camera_id,
                        zone_id=old_zone_id,
                        previous_zone_id=None,
                        confidence=confidence,
                        coordinates_json=coord_json,
                    )
                )
            elif old_zone_id is None and new_zone_id is not None:
                # Entered a zone from unzoned territory
                emitted_events.append(
                    TwinEvent(
                        event_id=generate_uuid7(),
                        event_type="ZONE_ENTERED",
                        timestamp=timestamp,
                        track_id=track_id,
                        person_id=person_id,
                        camera_id=camera_id,
                        zone_id=new_zone_id,
                        previous_zone_id=None,
                        confidence=confidence,
                        coordinates_json=coord_json,
                    )
                )
            elif old_zone_id is not None and new_zone_id is not None:
                # Direct cross-zone move
                emitted_events.append(
                    TwinEvent(
                        event_id=generate_uuid7(),
                        event_type="CROSS_ZONE_TRANSITION",
                        timestamp=timestamp,
                        track_id=track_id,
                        person_id=person_id,
                        camera_id=camera_id,
                        zone_id=new_zone_id,
                        previous_zone_id=old_zone_id,
                        confidence=confidence,
                        coordinates_json=coord_json,
                    )
                )
                emitted_events.append(
                    TwinEvent(
                        event_id=generate_uuid7(),
                        event_type="ZONE_ENTERED",
                        timestamp=timestamp,
                        track_id=track_id,
                        person_id=person_id,
                        camera_id=camera_id,
                        zone_id=new_zone_id,
                        previous_zone_id=old_zone_id,
                        confidence=confidence,
                        coordinates_json=coord_json,
                    )
                )

        # 6. Dwell time threshold evaluation
        updated_entity = self.state.get_track(track_id)
        if (
            updated_entity
            and updated_entity.current_zone_id
            and updated_entity.dwell_start
        ):
            dwell_start_dt = parse_utc(updated_entity.dwell_start)
            current_dt = parse_utc(timestamp)
            dwell_duration = (current_dt - dwell_start_dt).total_seconds()
            dwell_key = (track_id, updated_entity.current_zone_id, updated_entity.dwell_start)
            if dwell_duration > self.dwell_threshold_seconds and dwell_key not in self._dwell_alerted:
                self._dwell_alerted.add(dwell_key)
                emitted_events.append(
                    TwinEvent(
                        event_id=generate_uuid7(),
                        event_type="DWELL_TIME_EXCEEDED",
                        timestamp=timestamp,
                        track_id=track_id,
                        person_id=person_id,
                        camera_id=camera_id,
                        zone_id=updated_entity.current_zone_id,
                        confidence=confidence,
                        coordinates_json=coord_json,
                        payload_json=json.dumps({"dwell_seconds": dwell_duration}),
                    )
                )

        # 7. Persist to append-only SQLite ledger
        if self.position_sample_seconds > 0:
            now_dt = parse_utc(timestamp)
            last = self._last_pos_stored.get(track_id)
            if len(emitted_events) == 1 and last is not None and (now_dt - last).total_seconds() < self.position_sample_seconds:
                self.store.defer_event(emitted_events[0])     # plain position sample, no transition
                self.telemetry.record_ingest(1, (time.perf_counter() - start_time) * 1000.0)
                return emitted_events
            self._last_pos_stored[track_id] = now_dt
        for evt in emitted_events:
            seq_num = self.store.append_event(evt)
            evt.sequence_num = seq_num

            # Check snapshot checkpoint threshold
            if self.snapshot_manager.should_snapshot(seq_num):
                self.snapshot_manager.create_snapshot(self.state, seq_num, self.store)
                self.telemetry.record_snapshot()

        # 8. Record telemetry
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        self.telemetry.record_ingest(len(emitted_events), latency_ms)

        return emitted_events

    def prune_tracks(
        self,
        now_iso: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
        track_id_prefix: Optional[str] = None,
    ) -> List[TwinEvent]:
        """Prune inactive tracks exceeding TTL and emit TRACK_LOST events.

        track_id_prefix limits pruning to tracks whose ID starts with it (None = all tracks).
        """
        now_iso = canonical_utc(now_iso) if now_iso else utc_now_canonical()

        effective_ttl = self.track_ttl_seconds if ttl_seconds is None else ttl_seconds
        pruned_entities = self.state.prune_inactive_tracks(now_iso, effective_ttl, track_id_prefix)
        lost_events: List[TwinEvent] = []

        for entity in pruned_entities:
            self._last_pos_stored.pop(entity.track_id, None)
            evt = TwinEvent(
                event_id=generate_uuid7(),
                event_type="TRACK_LOST",
                timestamp=now_iso,
                track_id=entity.track_id,
                person_id=entity.person_id,
                camera_id=entity.current_camera_id,
                zone_id=entity.current_zone_id,
                coordinates_json=json.dumps({"x": entity.position[0], "y": entity.position[1]}) if entity.position else None,
            )
            seq = self.store.append_event(evt)
            evt.sequence_num = seq
            lost_events.append(evt)

        if lost_events:
            self.telemetry.record_ingest(len(lost_events), 0.0)

        return lost_events

    def replay_events(self, events: List[TwinEvent]) -> int:
        """Deterministically replay a list of events into TwinState without writing to the store.

        Returns:
            Number of events successfully applied.
        """
        count = 0
        for evt in events:
            pos = None
            if evt.coordinates_json:
                try:
                    c = json.loads(evt.coordinates_json)
                    pos = (float(c["x"]), float(c["y"]))
                except Exception:
                    pass

            if evt.event_type in ("PERSON_DETECTED", "ZONE_ENTERED", "CROSS_ZONE_TRANSITION"):
                self.state.update_track(
                    track_id=evt.track_id,
                    camera_id=evt.camera_id,
                    zone_id=evt.zone_id,
                    position=pos,
                    timestamp=evt.timestamp,
                    confidence=evt.confidence or 1.0,
                    person_id=evt.person_id,
                )
            elif evt.event_type == "ZONE_EXITED":
                self.state.update_track(
                    track_id=evt.track_id,
                    camera_id=evt.camera_id,
                    zone_id=None,
                    position=pos,
                    timestamp=evt.timestamp,
                    confidence=evt.confidence or 1.0,
                    person_id=evt.person_id,
                )
            elif evt.event_type == "TRACK_LOST":
                # Mirror prune_tracks exactly: a lost track leaves the active set. (Previously replay
                # re-created it as ACTIVE and only flagged a copy, so a restart resurrected lost
                # tracks and the next prune emitted a duplicate TRACK_LOST.)
                self.state.remove_track(evt.track_id)
            count += 1

        return count
