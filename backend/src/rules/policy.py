from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import yaml

try:
    from src.vision.seed import _resolve_data_path
except ImportError:
    from vision.seed import _resolve_data_path


def parse_utc_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp string, treating naive strings as UTC."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _validate_hh_mm(val: Any, field_name: str = "time") -> Tuple[int, int]:
    """Validate and parse HH:MM string, raising ValueError on malformed or out-of-range values."""
    if not isinstance(val, str):
        raise ValueError(f"Invalid {field_name}: expected HH:MM string, got {type(val).__name__}")
    parts = val.strip().split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        raise ValueError(f"Invalid {field_name}: expected HH:MM format (got '{val}')")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as e:
        raise ValueError(f"Invalid {field_name}: non-numeric digits in HH:MM (got '{val}')") from e
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid {field_name}: hour must be 0-23 and minute 0-59 (got '{val}')")
    return hour, minute


class RestrictedZonePolicy(dict):
    """Dictionary representing access rules for a restricted zone with attribute access."""

    def __init__(
        self,
        min_security_level: int = 1,
        authorized_person_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ):
        super().__init__(
            min_security_level=min_security_level,
            authorized_person_ids=list(authorized_person_ids or []),
            **kwargs,
        )

    @property
    def min_security_level(self) -> int:
        return self.get("min_security_level", 1)

    @property
    def authorized_person_ids(self) -> List[str]:
        return self.get("authorized_person_ids", [])


class BusinessHoursPolicy(dict):
    """Dictionary representing business hours window in UTC with attribute access."""

    def __init__(self, start: str = "08:00", end: str = "19:00", **kwargs: Any):
        super().__init__(start=start, end=end, **kwargs)

    @property
    def start(self) -> str:
        return self.get("start", "08:00")

    @property
    def end(self) -> str:
        return self.get("end", "19:00")


@dataclass
class AccessPolicy:
    """Access policy controlling zone authorization, business hours, and dwell anomaly thresholds."""

    version: int = 1
    restricted_zones: Dict[str, RestrictedZonePolicy] = field(default_factory=dict)
    business_hours_utc: BusinessHoursPolicy = field(
        default_factory=lambda: BusinessHoursPolicy(start="08:00", end="19:00")
    )
    dwell_alert_min_security_level: int = 3

    def __post_init__(self) -> None:
        # Normalize and validate restricted zones
        normalized_zones: Dict[str, RestrictedZonePolicy] = {}
        for zid, zcfg in self.restricted_zones.items():
            if isinstance(zcfg, RestrictedZonePolicy):
                normalized_zones[zid] = zcfg
            elif isinstance(zcfg, dict):
                normalized_zones[zid] = RestrictedZonePolicy(
                    min_security_level=int(zcfg.get("min_security_level", 1)),
                    authorized_person_ids=list(zcfg.get("authorized_person_ids", [])),
                )
            else:
                normalized_zones[zid] = RestrictedZonePolicy()
        self.restricted_zones = normalized_zones

        # Normalize and validate business hours
        if isinstance(self.business_hours_utc, BusinessHoursPolicy):
            s_val = self.business_hours_utc.start
            e_val = self.business_hours_utc.end
        elif isinstance(self.business_hours_utc, dict):
            s_val = str(self.business_hours_utc.get("start", "08:00"))
            e_val = str(self.business_hours_utc.get("end", "19:00"))
        else:
            s_val, e_val = "08:00", "19:00"

        _validate_hh_mm(s_val, "business_hours_utc.start")
        _validate_hh_mm(e_val, "business_hours_utc.end")
        self.business_hours_utc = BusinessHoursPolicy(start=s_val, end=e_val)

    def is_authorized(self, zone_id: str, person_id: Optional[str]) -> bool:
        """Return True if person_id is authorized to enter zone_id."""
        if zone_id not in self.restricted_zones:
            return True
        if person_id is None:
            return False
        return person_id in self.restricted_zones[zone_id].authorized_person_ids

    def is_outside_business_hours(self, dt: datetime) -> bool:
        """Return True if UTC timestamp falls strictly outside business hours [start, end]."""
        utc_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        utc_dt = utc_dt.astimezone(timezone.utc)
        t = utc_dt.time()

        s_h, s_m = _validate_hh_mm(self.business_hours_utc.start, "start")
        e_h, e_m = _validate_hh_mm(self.business_hours_utc.end, "end")
        start_t = time(s_h, s_m)
        end_t = time(e_h, e_m)

        if start_t <= end_t:
            return t < start_t or t > end_t
        else:
            # Overnight span (e.g. 22:00 to 06:00)
            return end_t < t < start_t


def load_access_policy(path: Union[str, Path] = "data/rules/access_policy.yaml") -> AccessPolicy:
    """Load access policy from YAML file, resolving path relative to project structure."""
    resolved_path = _resolve_data_path(str(path), str(path))
    if not resolved_path.exists():
        raise FileNotFoundError(f"Access policy configuration file not found at: {path}")

    with open(resolved_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Access policy YAML must contain a dictionary, got {type(data).__name__}")

    # Unknown keys are ignored; extract only known fields
    version = int(data.get("version", 1))

    raw_restricted = data.get("restricted_zones", {})
    restricted_zones: Dict[str, RestrictedZonePolicy] = {}
    if isinstance(raw_restricted, dict):
        for zone_id, zone_cfg in raw_restricted.items():
            if isinstance(zone_cfg, dict):
                restricted_zones[zone_id] = RestrictedZonePolicy(
                    min_security_level=int(zone_cfg.get("min_security_level", 1)),
                    authorized_person_ids=list(zone_cfg.get("authorized_person_ids", [])),
                )

    raw_hours = data.get("business_hours_utc", {})
    if isinstance(raw_hours, dict):
        start_str = raw_hours.get("start", "08:00")
        end_str = raw_hours.get("end", "19:00")
    else:
        start_str, end_str = "08:00", "19:00"

    _validate_hh_mm(start_str, "business_hours_utc.start")
    _validate_hh_mm(end_str, "business_hours_utc.end")
    business_hours = BusinessHoursPolicy(start=start_str, end=end_str)

    dwell_min_sec = int(data.get("dwell_alert_min_security_level", 3))

    return AccessPolicy(
        version=version,
        restricted_zones=restricted_zones,
        business_hours_utc=business_hours,
        dwell_alert_min_security_level=dwell_min_sec,
    )
