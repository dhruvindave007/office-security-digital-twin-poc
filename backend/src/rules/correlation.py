from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

try:
    from src.access.config import AccessConfig, Door
    from src.rules.policy import AccessPolicy, parse_utc_timestamp
    from src.twin.models import TwinEvent
    from src.twin.store import TwinStore
except ImportError:
    from access.config import AccessConfig, Door
    from rules.policy import AccessPolicy, parse_utc_timestamp
    from twin.models import TwinEvent
    from twin.store import TwinStore

if TYPE_CHECKING:
    from src.vision.topology import TopologyService

logger = logging.getLogger("office_vision.rules.correlation")


@dataclass
class IncidentCandidate:
    """Pure decision outcome produced by CorrelationEngine before persistence."""

    incident_type: str
    severity: str
    dedupe_key: Tuple
    event: TwinEvent
    zone_id: Optional[str]
    track_id: Optional[str]
    person_id: Optional[str]
    summary: str
    extra_event_ids: List[str] = field(default_factory=list)


class CorrelationEngine:
    """Cross-camera, access-control, and temporal correlation engine.

    LIMITATION & ARCHITECTURE NOTE:
    CorrelationEngine in-memory state is transient. It is NOT rebuilt on
    system restart and is NEVER fed by replay_events. Decisions rely purely
    on event timestamps (never wall-clock time).
    """

    def __init__(
        self,
        policy: AccessPolicy,
        access_config: AccessConfig,
        topology: "TopologyService",
        store: TwinStore,
    ):
        self.policy = policy
        self.access_config = access_config
        self.topology = topology
        self.store = store

        # in-memory state keyed by door_id and track_id
        # pending_grants[door_id] -> list of grant dicts
        self.pending_grants: Dict[str, List[Dict[str, Any]]] = {}
        # last_seen[track_id] -> (camera_id, datetime_utc, event_id)
        self.last_seen: Dict[str, Tuple[str, datetime, str]] = {}

    def _sweep_expired_grants(
        self, current_event_dt: datetime, current_event: TwinEvent
    ) -> List[IncidentCandidate]:
        """Sweep all doors for unused grants older than badge_entry_window_seconds.

        The sweep evaluates deterministically at the timestamp of an arriving event.
        """
        expired_candidates: List[IncidentCandidate] = []
        window_sec = self.policy.correlation.badge_entry_window_seconds

        for door_id, grants in list(self.pending_grants.items()):
            remaining_grants: List[Dict[str, Any]] = []
            door = self.access_config.doors.get(door_id)
            zone_id = door.zone_id if door else None

            for grant in grants:
                if grant["used_by_track"] is None:
                    elapsed = (current_event_dt - grant["timestamp"]).total_seconds()
                    if elapsed > window_sec:
                        badge_id = grant["badge_id"]
                        track_id = f"badge:{badge_id}"
                        summary = (
                            f"Badge {badge_id} was granted entry at {door_id} but "
                            f"no track entered zone {zone_id or 'unknown'} within {window_sec:.1f} s."
                        )
                        cand = IncidentCandidate(
                            incident_type="BADGE_WITHOUT_ENTRY",
                            severity="LOW",
                            dedupe_key=("BADGE_WITHOUT_ENTRY", zone_id, track_id),
                            event=grant["grant_event"],
                            zone_id=zone_id,
                            track_id=track_id,
                            person_id=grant["person_id"],
                            summary=summary,
                            extra_event_ids=[],
                        )
                        expired_candidates.append(cand)
                    else:
                        remaining_grants.append(grant)
                else:
                    # Keep used grants for potential tailgating evaluation
                    # Purge used grants older than tailgating window
                    used_elapsed = (current_event_dt - grant["used_at"]).total_seconds()
                    if used_elapsed <= self.policy.correlation.tailgating_window_seconds:
                        remaining_grants.append(grant)

            self.pending_grants[door_id] = remaining_grants

        return expired_candidates

    def evaluate(self, events: List[TwinEvent]) -> List[IncidentCandidate]:
        """Evaluate events against correlation rules and return candidate incidents."""
        candidates: List[IncidentCandidate] = []

        for event in events:
            current_event_dt = parse_utc_timestamp(event.timestamp)

            # 1. Sweep expired grants on EVERY event processed
            sweep_candidates = self._sweep_expired_grants(current_event_dt, event)
            candidates.extend(sweep_candidates)

            # 2. Bookkeeping: ACCESS_GRANTED
            if event.event_type == "ACCESS_GRANTED":
                door_id = None
                badge_id = None
                try:
                    payload = json.loads(event.payload_json or "{}")
                    door_id = payload.get("door_id")
                    badge_id = payload.get("badge_id")
                except Exception:
                    pass

                if not door_id and event.camera_id:
                    # Fallback door lookup
                    for d in self.access_config.doors.values():
                        if d.camera_id == event.camera_id and d.zone_id == event.zone_id:
                            door_id = d.door_id
                            break

                if not badge_id and event.track_id and event.track_id.startswith("badge:"):
                    badge_id = event.track_id.split(":", 1)[1]

                if door_id and door_id in self.access_config.doors:
                    door = self.access_config.doors[door_id]
                    if door.badge_required:
                        grant_record = {
                            "event_id": event.event_id,
                            "timestamp": current_event_dt,
                            "timestamp_str": event.timestamp,
                            "person_id": event.person_id,
                            "badge_id": badge_id or "",
                            "used_by_track": None,
                            "used_at": None,
                            "grant_event": event,
                        }
                        self.pending_grants.setdefault(door_id, []).append(grant_record)

            # 3. Correlation on ZONE_ENTERED
            elif event.event_type == "ZONE_ENTERED" and event.zone_id:
                # Find badge-required doors for this zone
                badge_doors = [
                    d
                    for d in self.access_config.doors.values()
                    if d.zone_id == event.zone_id and d.badge_required
                ]

                for door in badge_doors:
                    grants = self.pending_grants.get(door.door_id, [])
                    window_sec = self.policy.correlation.badge_entry_window_seconds
                    tailgate_win_sec = self.policy.correlation.tailgating_window_seconds

                    # Look for earliest unused grant within badge_entry_window_seconds
                    matched_grant = None
                    for grant in grants:
                        if grant["used_by_track"] is None:
                            dt_diff = (current_event_dt - grant["timestamp"]).total_seconds()
                            if 0.0 <= dt_diff <= window_sec:
                                matched_grant = grant
                                break

                    if matched_grant is not None:
                        # Legitimate badge-admitted entry
                        matched_grant["used_by_track"] = event.track_id
                        matched_grant["used_at"] = current_event_dt
                    else:
                        # Check for TAILGATING: a grant at this door used by a different track within tailgating_window
                        tailgate_grant = None
                        tailgate_diff = 0.0
                        for grant in grants:
                            if (
                                grant["used_by_track"] is not None
                                and grant["used_by_track"] != event.track_id
                                and grant["used_at"] is not None
                            ):
                                diff = (current_event_dt - grant["used_at"]).total_seconds()
                                if 0.0 <= diff <= tailgate_win_sec:
                                    tailgate_grant = grant
                                    tailgate_diff = diff
                                    break

                        if tailgate_grant is not None:
                            # Raise TAILGATING
                            badge_name = tailgate_grant["badge_id"]
                            prior_trk = tailgate_grant["used_by_track"]
                            summary = (
                                f"Track {event.track_id} entered {event.zone_id} "
                                f"{tailgate_diff:.1f} s after badge {badge_name} admitted "
                                f"track {prior_trk}; no separate badge event matched."
                            )
                            candidates.append(
                                IncidentCandidate(
                                    incident_type="TAILGATING",
                                    severity="HIGH",
                                    dedupe_key=("TAILGATING", event.zone_id, event.track_id),
                                    event=event,
                                    zone_id=event.zone_id,
                                    track_id=event.track_id,
                                    person_id=event.person_id,
                                    summary=summary,
                                    extra_event_ids=[tailgate_grant["event_id"]],
                                )
                            )
                        else:
                            # Raise ACCESS_WITHOUT_BADGE
                            summary = (
                                f"Track {event.track_id} entered restricted zone {event.zone_id} "
                                f"at {door.door_id} without a valid badge grant."
                            )
                            candidates.append(
                                IncidentCandidate(
                                    incident_type="ACCESS_WITHOUT_BADGE",
                                    severity="MEDIUM",
                                    dedupe_key=(
                                        "ACCESS_WITHOUT_BADGE",
                                        event.zone_id,
                                        event.track_id,
                                    ),
                                    event=event,
                                    zone_id=event.zone_id,
                                    track_id=event.track_id,
                                    person_id=event.person_id,
                                    summary=summary,
                                    extra_event_ids=[],
                                )
                            )

            # 4. Correlation on PERSON_DETECTED (Topology Feasibility)
            elif event.event_type == "PERSON_DETECTED" and event.camera_id:
                if event.track_id in self.last_seen:
                    prev_cam, prev_ts, prev_event_id = self.last_seen[event.track_id]
                    if prev_cam != event.camera_id:
                        elapsed = (current_event_dt - prev_ts).total_seconds()
                        min_transit = self.topology.min_transit_seconds(prev_cam, event.camera_id)
                        slack = self.policy.correlation.transit_slack_seconds

                        if min_transit is None:
                            # Unreachable cameras
                            summary = (
                                f"Track {event.track_id} was observed on {event.camera_id} "
                                f"{elapsed:.1f} s after {prev_cam}; cameras are topologically unreachable. "
                                f"Possible tracking or ID-association error."
                            )
                            candidates.append(
                                IncidentCandidate(
                                    incident_type="TRACK_DISCONTINUITY",
                                    severity="LOW",
                                    dedupe_key=("TRACK_DISCONTINUITY", event.track_id),
                                    event=event,
                                    zone_id=event.zone_id,
                                    track_id=event.track_id,
                                    person_id=event.person_id,
                                    summary=summary,
                                    extra_event_ids=[prev_event_id],
                                )
                            )
                        elif elapsed < (min_transit - slack):
                            # Physically impossible transit speed
                            summary = (
                                f"Track {event.track_id} was observed on {event.camera_id} "
                                f"{elapsed:.1f} s after {prev_cam}; topology minimum is "
                                f"{min_transit:.1f} s. Possible tracking or ID-association error."
                            )
                            candidates.append(
                                IncidentCandidate(
                                    incident_type="TRACK_DISCONTINUITY",
                                    severity="LOW",
                                    dedupe_key=("TRACK_DISCONTINUITY", event.track_id),
                                    event=event,
                                    zone_id=event.zone_id,
                                    track_id=event.track_id,
                                    person_id=event.person_id,
                                    summary=summary,
                                    extra_event_ids=[prev_event_id],
                                )
                            )

                # Always update last_seen for track on PERSON_DETECTED
                self.last_seen[event.track_id] = (
                    event.camera_id,
                    current_event_dt,
                    event.event_id,
                )

        return candidates
