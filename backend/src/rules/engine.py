import json
import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

try:
    from src.twin.engine import generate_uuid7
    from src.twin.models import Incident, TwinEvent
    from src.twin.state import TwinState
    from src.twin.store import TwinStore
    from src.rules.policy import AccessPolicy, parse_utc_timestamp
except ImportError:
    from twin.engine import generate_uuid7
    from twin.models import Incident, TwinEvent
    from twin.state import TwinState
    from twin.store import TwinStore
    from rules.policy import AccessPolicy, parse_utc_timestamp

if TYPE_CHECKING:
    try:
        from src.rules.correlation import CorrelationEngine
    except ImportError:
        from rules.correlation import CorrelationEngine

logger = logging.getLogger("office_vision.rules.engine")


class IncidentRuleEngine:
    """Deterministic, evidence-grounded incident evaluation engine."""

    def __init__(
        self,
        store: TwinStore,
        state: TwinState,
        policy: AccessPolicy,
        correlation: Optional["CorrelationEngine"] = None,
    ):
        self.store = store
        self.state = state
        self.policy = policy
        self.correlation = correlation
        self._open_incidents: Dict[Tuple, Incident] = {}
        self._load_open_incidents()

    def _get_dedupe_key(
        self, incident_type: str, zone_id: Optional[str], track_id: Optional[str]
    ) -> Optional[Tuple]:
        """Compute the unique deduplication key for an open incident."""
        if incident_type == "UNAUTHORIZED_ENTRY":
            return (incident_type, zone_id, track_id)
        elif incident_type == "ZONE_OVER_CAPACITY":
            return (incident_type, zone_id)
        elif incident_type == "DWELL_ANOMALY":
            return (incident_type, zone_id, track_id)
        elif incident_type == "AFTER_HOURS_BREACH":
            return (incident_type, track_id)
        elif incident_type == "ACCESS_WITHOUT_BADGE":
            return (incident_type, zone_id, track_id)
        elif incident_type == "TAILGATING":
            return (incident_type, zone_id, track_id)
        elif incident_type == "BADGE_WITHOUT_ENTRY":
            return (incident_type, zone_id, track_id)
        elif incident_type == "TRACK_DISCONTINUITY":
            return (incident_type, track_id)
        return None

    def _load_open_incidents(self) -> None:
        """Load open and investigating incidents from ledger into in-memory dedupe registry on startup/restart."""
        open_list = self.store.get_incidents(status="OPEN", limit=500)
        investigating_list = self.store.get_incidents(status="INVESTIGATING", limit=500)
        active_list = open_list + investigating_list
        active_list.sort(key=lambda inc: inc.start_time)
        for inc in active_list:
            key = self._get_dedupe_key(inc.incident_type, inc.zone_id, inc.track_id)
            if key:
                self._open_incidents[key] = inc

    def forget(self, incident_id: str) -> None:
        """Remove an incident from the open deduplication index by incident_id."""
        to_delete = [
            k for k, inc in self._open_incidents.items() if inc.incident_id == incident_id
        ]
        for k in to_delete:
            del self._open_incidents[k]

    @staticmethod
    def _append_supporting_event(incident: Incident, event: TwinEvent) -> None:
        """Append event_id to incident's supporting_event_ids_json without duplicates."""
        IncidentRuleEngine._append_event_id(incident, event.event_id, event.timestamp)

    @staticmethod
    def _append_event_id(incident: Incident, event_id: str, timestamp: str) -> None:
        """Append an event_id to incident's supporting_event_ids_json without duplicates."""
        try:
            ids = json.loads(incident.supporting_event_ids_json or "[]")
        except Exception:
            ids = []
        if not isinstance(ids, list):
            ids = []
        if event_id not in ids:
            ids.append(event_id)
        incident.supporting_event_ids_json = json.dumps(ids)
        incident.updated_at = timestamp

    def _raise_or_append(
        self,
        incident_type: str,
        severity: str,
        dedupe_key: Tuple,
        event: TwinEvent,
        zone_id: Optional[str],
        track_id: Optional[str],
        person_id: Optional[str],
        summary: str,
        changed: Dict[str, Incident],
        extra_event_ids: Optional[List[str]] = None,
    ) -> Incident:
        """Create a new incident or append evidence to an existing open incident."""
        if dedupe_key in self._open_incidents:
            inc = self._open_incidents[dedupe_key]
            self._append_supporting_event(inc, event)
            if extra_event_ids:
                for eid in extra_event_ids:
                    self._append_event_id(inc, eid, event.timestamp)
            changed[inc.incident_id] = inc
            return inc
        else:
            all_event_ids = [event.event_id]
            if extra_event_ids:
                for eid in extra_event_ids:
                    if eid not in all_event_ids:
                        all_event_ids.append(eid)
            inc = Incident(
                incident_id=generate_uuid7(),
                incident_type=incident_type,
                severity=severity,
                status="OPEN",
                zone_id=zone_id,
                track_id=track_id,
                person_id=person_id,
                start_time=event.timestamp,
                end_time=None,
                supporting_event_ids_json=json.dumps(all_event_ids),
                summary=summary,
                created_at=event.timestamp,
                updated_at=event.timestamp,
            )
            self._open_incidents[dedupe_key] = inc
            changed[inc.incident_id] = inc
            return inc

    def evaluate(self, events: List[TwinEvent]) -> List[Incident]:
        """Evaluate events against security rules and return created or updated incidents."""
        changed: Dict[str, Incident] = {}

        for event in events:
            # 1. UNAUTHORIZED_ENTRY
            if event.event_type == "ZONE_ENTERED" and event.zone_id:
                if event.zone_id in self.policy.restricted_zones:
                    cfg = self.policy.restricted_zones[event.zone_id]
                    if event.person_id is None or event.person_id not in cfg.authorized_person_ids:
                        key = ("UNAUTHORIZED_ENTRY", event.zone_id, event.track_id)
                        person_desc = f"person: {event.person_id}" if event.person_id else "person: unknown"
                        summary = f"Track {event.track_id} ({person_desc}) entered restricted zone {event.zone_id}."
                        self._raise_or_append(
                            incident_type="UNAUTHORIZED_ENTRY",
                            severity="HIGH",
                            dedupe_key=key,
                            event=event,
                            zone_id=event.zone_id,
                            track_id=event.track_id,
                            person_id=event.person_id,
                            summary=summary,
                            changed=changed,
                        )

            # 2. ZONE_OVER_CAPACITY Trigger
            if event.event_type == "ZONE_ENTERED" and event.zone_id:
                zone = self.store.get_zone(event.zone_id)
                if zone and zone.max_occupancy is not None:
                    occupancy = self.state.get_zone_occupancy(event.zone_id)
                    if occupancy > zone.max_occupancy:
                        key = ("ZONE_OVER_CAPACITY", event.zone_id)
                        summary = f"Zone {event.zone_id} occupancy ({occupancy}) exceeded maximum capacity ({zone.max_occupancy})."
                        self._raise_or_append(
                            incident_type="ZONE_OVER_CAPACITY",
                            severity="MEDIUM",
                            dedupe_key=key,
                            event=event,
                            zone_id=event.zone_id,
                            track_id=event.track_id,
                            person_id=event.person_id,
                            summary=summary,
                            changed=changed,
                        )

            # 2b. ZONE_OVER_CAPACITY Auto-resolve
            resolved_zone = None
            if event.event_type == "CROSS_ZONE_TRANSITION":
                resolved_zone = event.previous_zone_id
            elif event.event_type in ("ZONE_EXITED", "TRACK_LOST"):
                resolved_zone = event.zone_id

            if resolved_zone:
                zone = self.store.get_zone(resolved_zone)
                if zone and zone.max_occupancy is not None:
                    occupancy = self.state.get_zone_occupancy(resolved_zone)
                    if occupancy <= zone.max_occupancy:
                        key = ("ZONE_OVER_CAPACITY", resolved_zone)
                        if key in self._open_incidents:
                            inc = self._open_incidents.pop(key)
                            inc.status = "RESOLVED"
                            inc.end_time = event.timestamp
                            inc.updated_at = event.timestamp
                            self._append_supporting_event(inc, event)
                            changed[inc.incident_id] = inc

            # 3. DWELL_ANOMALY
            if event.event_type == "DWELL_TIME_EXCEEDED" and event.zone_id:
                zone = self.store.get_zone(event.zone_id)
                if zone and zone.security_level >= self.policy.dwell_alert_min_security_level:
                    key = ("DWELL_ANOMALY", event.zone_id, event.track_id)
                    person_desc = f" (person: {event.person_id})" if event.person_id else ""
                    summary = f"Track {event.track_id}{person_desc} exceeded dwell time threshold in zone {event.zone_id}."
                    self._raise_or_append(
                        incident_type="DWELL_ANOMALY",
                        severity="MEDIUM",
                        dedupe_key=key,
                        event=event,
                        zone_id=event.zone_id,
                        track_id=event.track_id,
                        person_id=event.person_id,
                        summary=summary,
                        changed=changed,
                    )

            # 4. AFTER_HOURS_BREACH
            if event.event_type == "PERSON_DETECTED":
                event_dt = parse_utc_timestamp(event.timestamp)
                if self.policy.is_outside_business_hours(event_dt):
                    key = ("AFTER_HOURS_BREACH", event.track_id)
                    person_desc = f" (person: {event.person_id})" if event.person_id else ""
                    summary = f"Track {event.track_id}{person_desc} detected outside business hours."
                    self._raise_or_append(
                        incident_type="AFTER_HOURS_BREACH",
                        severity="LOW",
                        dedupe_key=key,
                        event=event,
                        zone_id=event.zone_id,
                        track_id=event.track_id,
                        person_id=event.person_id,
                        summary=summary,
                        changed=changed,
                    )

        # 5. Correlation rules evaluation
        if self.correlation is not None:
            candidates = self.correlation.evaluate(events)
            for cand in candidates:
                self._raise_or_append(
                    incident_type=cand.incident_type,
                    severity=cand.severity,
                    dedupe_key=cand.dedupe_key,
                    event=cand.event,
                    zone_id=cand.zone_id,
                    track_id=cand.track_id,
                    person_id=cand.person_id,
                    summary=cand.summary,
                    changed=changed,
                    extra_event_ids=cand.extra_event_ids,
                )

        # Persist all created or modified incidents
        for inc in changed.values():
            self.store.insert_incident(inc)

        return list(changed.values())
