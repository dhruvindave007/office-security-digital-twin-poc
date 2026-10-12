import json
import logging
from typing import List, Optional, Tuple, TYPE_CHECKING

try:
    from src.access.config import AccessConfig
    from src.rules.policy import parse_utc_timestamp
    from src.twin.engine import generate_uuid7
    from src.twin.models import Incident, TwinEvent
    from src.twin.store import TwinStore
except ImportError:
    from access.config import AccessConfig
    from rules.policy import parse_utc_timestamp
    from twin.engine import generate_uuid7
    from twin.models import Incident, TwinEvent
    from twin.store import TwinStore

if TYPE_CHECKING:
    try:
        from src.rules.engine import IncidentRuleEngine
    except ImportError:
        from rules.engine import IncidentRuleEngine

logger = logging.getLogger("office_vision.access.service")


class AccessControlService:
    """Access control service for managing badge readers and ingesting door swipe events."""

    def __init__(
        self,
        store: TwinStore,
        config: AccessConfig,
        rule_engine: Optional["IncidentRuleEngine"] = None,
    ):
        self.store = store
        self.config = config
        self.rule_engine = rule_engine

        # Validate that each door's zone_id and camera_id exist in TwinStore
        known_zones = {z.zone_id for z in self.store.get_zones()}
        known_cameras = {c.camera_id for c in self.store.get_cameras()}

        for door_id, door in self.config.doors.items():
            if door.zone_id not in known_zones:
                raise ValueError(
                    f"Invalid configuration for door '{door_id}': unknown zone_id '{door.zone_id}'"
                )
            if door.camera_id not in known_cameras:
                raise ValueError(
                    f"Invalid configuration for door '{door_id}': unknown camera_id '{door.camera_id}'"
                )

    def ingest(
        self,
        door_id: str,
        badge_id: str,
        result: str,
        timestamp: str,
    ) -> Tuple[TwinEvent, List[Incident]]:
        """Ingest a physical access-control swipe event into the immutable event ledger.

        Args:
            door_id: Identified reader door ID.
            badge_id: Scanned credential/badge ID.
            result: Outcome ('GRANTED' or 'DENIED').
            timestamp: ISO-8601 timestamp string.

        Returns:
            Tuple of (appended TwinEvent, list of newly evaluated Incident objects).
        """
        normalized_result = result.strip().upper()
        if normalized_result not in ("GRANTED", "DENIED"):
            raise ValueError(
                f"Invalid access result '{result}'. Must be 'GRANTED' or 'DENIED'."
            )

        if door_id not in self.config.doors:
            raise KeyError(f"Unknown door_id '{door_id}'")

        door = self.config.doors[door_id]
        event_type = f"ACCESS_{normalized_result}"

        # Normalize timestamp to UTC ISO-8601
        dt = parse_utc_timestamp(timestamp)
        normalized_ts = dt.isoformat()

        person_id = self.config.badges.get(badge_id)
        payload = {
            "door_id": door_id,
            "badge_id": badge_id,
            "result": normalized_result,
        }

        evt = TwinEvent(
            event_id=generate_uuid7(),
            event_type=event_type,
            timestamp=normalized_ts,
            track_id=f"badge:{badge_id}",
            person_id=person_id,
            camera_id=door.camera_id,
            zone_id=door.zone_id,
            confidence=1.0,
            payload_json=json.dumps(payload),
        )

        # Append strictly to TwinStore event ledger
        seq_num = self.store.append_event(evt)
        evt.sequence_num = seq_num

        # Evaluate against rule engine if provided
        incidents: List[Incident] = []
        if self.rule_engine is not None:
            incidents = self.rule_engine.evaluate([evt])

        return evt, incidents
