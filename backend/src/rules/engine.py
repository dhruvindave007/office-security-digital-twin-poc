import json
import logging
from typing import Any, Dict, List, Optional, Tuple

try:
    from twin.engine import generate_uuid7
    from twin.models import Incident, TwinEvent
    from twin.state import TwinState
    from twin.store import TwinStore
    from rules.policy import AccessPolicy, parse_utc_timestamp
except ImportError:
    from src.twin.engine import generate_uuid7
    from src.twin.models import Incident, TwinEvent
    from src.twin.state import TwinState
    from src.twin.store import TwinStore
    from src.rules.policy import AccessPolicy, parse_utc_timestamp

logger = logging.getLogger("office_vision.rules.engine")


class IncidentRuleEngine:
    """Deterministic, evidence-grounded incident evaluation engine."""

    def __init__(self, store: TwinStore, state: TwinState, policy: AccessPolicy):
        self.store = store
        self.state = state
        self.policy = policy
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
        return None

    def _load_open_incidents(self) -> None:
        """Load open incidents from ledger into in-memory dedupe registry on startup/restart."""
        open_list = self.store.get_incidents(status="OPEN", limit=500)
        # Iterate in reverse (oldest to newest) so latest records establish current state
        for inc in reversed(open_list):
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
        try:
            ids = json.loads(incident.supporting_event_ids_json or "[]")
        except Exception:
            ids = []
        if not isinstance(ids, list):
            ids = []
        if event.event_id not in ids:
            ids.append(event.event_id)
        incident.supporting_event_ids_json = json.dumps(ids)
        incident.updated_at = event.timestamp

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
                        if key in self._open_incidents:
                            inc = self._open_incidents[key]
                            self._append_supporting_event(inc, event)
                            changed[inc.incident_id] = inc
                        else:
                            person_desc = f"person: {event.person_id}" if event.person_id else "person: unknown"
                            summary = f"Track {event.track_id} ({person_desc}) entered restricted zone {event.zone_id}."
                            inc = Incident(
                                incident_id=generate_uuid7(),
                                incident_type="UNAUTHORIZED_ENTRY",
                                severity="HIGH",
                                status="OPEN",
                                zone_id=event.zone_id,
                                track_id=event.track_id,
                                person_id=event.person_id,
                                start_time=event.timestamp,
                                end_time=None,
                                supporting_event_ids_json=json.dumps([event.event_id]),
                                summary=summary,
                                created_at=event.timestamp,
                                updated_at=event.timestamp,
                            )
                            self._open_incidents[key] = inc
                            changed[inc.incident_id] = inc

            # 2. ZONE_OVER_CAPACITY Trigger
            if event.event_type == "ZONE_ENTERED" and event.zone_id:
                zone = self.store.get_zone(event.zone_id)
                if zone and zone.max_occupancy is not None:
                    occupancy = self.state.get_zone_occupancy(event.zone_id)
                    if occupancy > zone.max_occupancy:
                        key = ("ZONE_OVER_CAPACITY", event.zone_id)
                        if key in self._open_incidents:
                            inc = self._open_incidents[key]
                            self._append_supporting_event(inc, event)
                            changed[inc.incident_id] = inc
                        else:
                            summary = f"Zone {event.zone_id} occupancy ({occupancy}) exceeded maximum capacity ({zone.max_occupancy})."
                            inc = Incident(
                                incident_id=generate_uuid7(),
                                incident_type="ZONE_OVER_CAPACITY",
                                severity="MEDIUM",
                                status="OPEN",
                                zone_id=event.zone_id,
                                track_id=event.track_id,
                                person_id=event.person_id,
                                start_time=event.timestamp,
                                end_time=None,
                                supporting_event_ids_json=json.dumps([event.event_id]),
                                summary=summary,
                                created_at=event.timestamp,
                                updated_at=event.timestamp,
                            )
                            self._open_incidents[key] = inc
                            changed[inc.incident_id] = inc

            # 2b. ZONE_OVER_CAPACITY Auto-resolve
            if event.event_type in ("ZONE_EXITED", "TRACK_LOST"):
                resolved_zone = event.zone_id or event.previous_zone_id
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
                    if key in self._open_incidents:
                        inc = self._open_incidents[key]
                        self._append_supporting_event(inc, event)
                        changed[inc.incident_id] = inc
                    else:
                        person_desc = f" (person: {event.person_id})" if event.person_id else ""
                        summary = f"Track {event.track_id}{person_desc} exceeded dwell time threshold in zone {event.zone_id}."
                        inc = Incident(
                            incident_id=generate_uuid7(),
                            incident_type="DWELL_ANOMALY",
                            severity="MEDIUM",
                            status="OPEN",
                            zone_id=event.zone_id,
                            track_id=event.track_id,
                            person_id=event.person_id,
                            start_time=event.timestamp,
                            end_time=None,
                            supporting_event_ids_json=json.dumps([event.event_id]),
                            summary=summary,
                            created_at=event.timestamp,
                            updated_at=event.timestamp,
                        )
                        self._open_incidents[key] = inc
                        changed[inc.incident_id] = inc

            # 4. AFTER_HOURS_BREACH
            if event.event_type == "PERSON_DETECTED":
                event_dt = parse_utc_timestamp(event.timestamp)
                if self.policy.is_outside_business_hours(event_dt):
                    key = ("AFTER_HOURS_BREACH", event.track_id)
                    if key in self._open_incidents:
                        inc = self._open_incidents[key]
                        self._append_supporting_event(inc, event)
                        changed[inc.incident_id] = inc
                    else:
                        person_desc = f" (person: {event.person_id})" if event.person_id else ""
                        summary = f"Track {event.track_id}{person_desc} detected outside business hours."
                        inc = Incident(
                            incident_id=generate_uuid7(),
                            incident_type="AFTER_HOURS_BREACH",
                            severity="LOW",
                            status="OPEN",
                            zone_id=event.zone_id,
                            track_id=event.track_id,
                            person_id=event.person_id,
                            start_time=event.timestamp,
                            end_time=None,
                            supporting_event_ids_json=json.dumps([event.event_id]),
                            summary=summary,
                            created_at=event.timestamp,
                            updated_at=event.timestamp,
                        )
                        self._open_incidents[key] = inc
                        changed[inc.incident_id] = inc

        # Persist all created or modified incidents
        for inc in changed.values():
            self.store.insert_incident(inc)

        return list(changed.values())
