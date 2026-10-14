from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

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
        entry_filter: Optional[Callable[[TwinEvent], bool]] = None,
    ):
        self.policy = policy
        # Optional site hook: return False for ZONE_ENTERED events that are not real door entries
        # (e.g. a track that first appears in the middle of a room after an occlusion).
        self.entry_filter = entry_filter
        # Entries waiting up to grant_after_entry_tolerance_seconds for a late badge grant.
        self.pending_entries: List[Dict[str, Any]] = []
        # Badge -> track matches made during the last evaluate() call: the rule engine attaches the
        # badge holder's identity to the admitted track and checks it against the room's allow list.
        self.attributions: List[Dict[str, Any]] = []
        self.access_config = access_config
        self.topology = topology
        self.store = store

        # in-memory state keyed by door_id and track_id
        # pending_grants[door_id] -> list of grant dicts
        self.pending_grants: Dict[str, List[Dict[str, Any]]] = {}
        # last_seen[track_id] -> (camera_id, datetime_utc, event_id)
        self.last_seen: Dict[str, Tuple[str, datetime, str]] = {}

    def handles_entry(self, zone_id: Optional[str]) -> bool:
        """True when entries into this zone are decided by badge correlation (it has a badge door)."""
        return any(d.zone_id == zone_id and d.badge_required for d in self.access_config.doors.values())

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

    def _unmatched_entry(self, event: TwinEvent, current_event_dt: datetime, badge_doors) -> IncidentCandidate:
        """Decide an entry that no badge grant matched: TAILGATING if a recently used grant exists, else ACCESS_WITHOUT_BADGE."""
        tailgate_win_sec = self.policy.correlation.tailgating_window_seconds
        door_grants = [(door, grant) for door in badge_doors for grant in self.pending_grants.get(door.door_id, [])]
        # 2) tailgating: a grant at any of these doors recently used by a DIFFERENT track
        tailgate_grant = None
        tailgate_diff = 0.0
        for _door, grant in door_grants:
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
            badge_name = tailgate_grant["badge_id"]
            prior_trk = tailgate_grant["used_by_track"]
            # Spell out both reference times: an earlier wording ("2.0 s after badge X
            # admitted track Y") was misread by the AI as 2.0 s after the badge grant.
            summary = (
                f"Track {event.track_id} entered {event.zone_id} at "
                f"{current_event_dt.strftime('%H:%M:%S')}, {tailgate_diff:.1f} s after track "
                f"{prior_trk} entered; {prior_trk} was admitted by badge {badge_name} granted at "
                f"{tailgate_grant['timestamp'].strftime('%H:%M:%S')}. No badge grant matched "
                f"track {event.track_id}."
            )
            return (
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
            # 3) at most one ACCESS_WITHOUT_BADGE per entry
            door_ids = ", ".join(d.door_id for d in badge_doors)
            summary = (
                f"Track {event.track_id} entered restricted zone {event.zone_id} "
                f"without a matching badge grant at {door_ids}."
            )
            return (
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


    def evaluate(self, events: List[TwinEvent]) -> List[IncidentCandidate]:
        """Evaluate events against correlation rules and return candidate incidents."""
        candidates: List[IncidentCandidate] = []
        self.attributions = []

        for event in events:
            current_event_dt = parse_utc_timestamp(event.timestamp)

            # 1. Sweep expired grants on EVERY event processed
            sweep_candidates = self._sweep_expired_grants(current_event_dt, event)
            candidates.extend(sweep_candidates)
            tol = self.policy.correlation.grant_after_entry_tolerance_seconds
            still = []
            for pe in self.pending_entries:
                if (current_event_dt - pe["dt"]).total_seconds() > tol:
                    candidates.append(self._unmatched_entry(pe["event"], pe["dt"], pe["badge_doors"]))
                else:
                    still.append(pe)
            self.pending_entries = still

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
                        tol = self.policy.correlation.grant_after_entry_tolerance_seconds
                        for pe in sorted(self.pending_entries, key=lambda e: e["dt"]):
                            if pe["event"].zone_id == door.zone_id and 0.0 <= (current_event_dt - pe["dt"]).total_seconds() <= tol:
                                grant_record["used_by_track"] = pe["event"].track_id
                                self.attributions.append({"entry": pe["event"], "grant": grant_record})
                                grant_record["used_at"] = pe["dt"]
                                self.pending_entries.remove(pe)
                                break

            # 3. Correlation on ZONE_ENTERED
            elif event.event_type == "ZONE_ENTERED" and event.zone_id and (self.entry_filter is None or self.entry_filter(event)):
                # A zone may have several badge-required doors: decide ONCE per entry across all of
                # them (previously each door was judged separately, so a valid badge at door A still
                # raised ACCESS_WITHOUT_BADGE for door B).
                badge_doors = [
                    d
                    for d in self.access_config.doors.values()
                    if d.zone_id == event.zone_id and d.badge_required
                ]
                if badge_doors:
                    window_sec = self.policy.correlation.badge_entry_window_seconds
                    tailgate_win_sec = self.policy.correlation.tailgating_window_seconds
                    door_grants = [
                        (door, grant)
                        for door in badge_doors
                        for grant in self.pending_grants.get(door.door_id, [])
                    ]

                    # 1) earliest unused grant within the entry window, across all doors
                    matched_grant = None
                    for _door, grant in sorted(door_grants, key=lambda dg: dg[1]["timestamp"]):
                        if grant["used_by_track"] is None:
                            dt_diff = (current_event_dt - grant["timestamp"]).total_seconds()
                            if 0.0 <= dt_diff <= window_sec:
                                matched_grant = grant
                                break

                    if matched_grant is not None:
                        # Legitimate badge-admitted entry
                        matched_grant["used_by_track"] = event.track_id
                        self.attributions.append({"entry": event, "grant": matched_grant})
                        matched_grant["used_at"] = current_event_dt
                    else:
                        tol = self.policy.correlation.grant_after_entry_tolerance_seconds
                        if tol > 0:
                            # Cameras often see a person at the door a moment BEFORE the badge event is
                            # logged; wait up to `tol` seconds for a late grant before deciding.
                            self.pending_entries.append({"event": event, "dt": current_event_dt, "badge_doors": badge_doors})
                        else:
                            candidates.append(self._unmatched_entry(event, current_event_dt, badge_doors))

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
