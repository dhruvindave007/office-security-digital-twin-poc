# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import yaml

try:
    from src.core.paths import _resolve_data_path
    from src.core.timeutil import parse_utc
except ImportError:
    from core.paths import _resolve_data_path
    from core.timeutil import parse_utc


def parse_utc_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp string, treating naive strings as UTC."""
    return parse_utc(ts)


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


class CorrelationPolicy(dict):
    """Dictionary representing correlation parameters with attribute access."""

    def __init__(
        self,
        badge_entry_window_seconds: float = 15.0,
        tailgating_window_seconds: float = 10.0,
        transit_slack_seconds: float = 2.0,
        grant_after_entry_tolerance_seconds: float = 0.0,
        **kwargs: Any,
    ):
        super().__init__(
            badge_entry_window_seconds=float(badge_entry_window_seconds),
            tailgating_window_seconds=float(tailgating_window_seconds),
            transit_slack_seconds=float(transit_slack_seconds),
            grant_after_entry_tolerance_seconds=float(grant_after_entry_tolerance_seconds),
            **kwargs,
        )

    @property
    def badge_entry_window_seconds(self) -> float:
        return float(self.get("badge_entry_window_seconds", 15.0))

    @property
    def tailgating_window_seconds(self) -> float:
        return float(self.get("tailgating_window_seconds", 10.0))

    @property
    def grant_after_entry_tolerance_seconds(self) -> float:
        """How long an entry may wait for a badge event logged AFTER the camera saw the person (0 = off)."""
        return float(self.get("grant_after_entry_tolerance_seconds", 0.0))

    @property
    def transit_slack_seconds(self) -> float:
        return float(self.get("transit_slack_seconds", 2.0))


@dataclass
class AccessPolicy:
    """Access policy controlling zone authorization, business hours, and dwell anomaly thresholds."""

    version: int = 1
    restricted_zones: Dict[str, RestrictedZonePolicy] = field(default_factory=dict)
    business_hours_utc: BusinessHoursPolicy = field(
        default_factory=lambda: BusinessHoursPolicy(start="08:00", end="19:00")
    )
    dwell_alert_min_security_level: int = 3
    correlation: CorrelationPolicy = field(
        default_factory=lambda: CorrelationPolicy()
    )

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

        # Normalize and validate correlation
        if isinstance(self.correlation, CorrelationPolicy):
            pass
        elif isinstance(self.correlation, dict):
            self.correlation = CorrelationPolicy(
                badge_entry_window_seconds=float(self.correlation.get("badge_entry_window_seconds", 15.0)),
                tailgating_window_seconds=float(self.correlation.get("tailgating_window_seconds", 10.0)),
                transit_slack_seconds=float(self.correlation.get("transit_slack_seconds", 2.0)),
            )
        else:
            self.correlation = CorrelationPolicy()

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
    # A malformed restricted zone must fail loudly: silently skipping it leaves the zone UNPROTECTED.
    if raw_restricted is None:
        raw_restricted = {}
    if not isinstance(raw_restricted, dict):
        raise ValueError("restricted_zones must be a mapping of zone_id -> {authorized_person_ids: [...]}")
    for zone_id, zone_cfg in raw_restricted.items():
        if zone_cfg is None:
            zone_cfg = {}  # "zone_x:" with no body = restricted, nobody authorized
        if not isinstance(zone_cfg, dict):
            raise ValueError(f"restricted_zones.{zone_id} must be a mapping, got {type(zone_cfg).__name__}")
        authorized = zone_cfg.get("authorized_person_ids", [])
        if authorized is None:
            authorized = []
        if not isinstance(authorized, list) or not all(isinstance(p, str) for p in authorized):
            raise ValueError(
                f"restricted_zones.{zone_id}.authorized_person_ids must be a list of strings "
                f"(a single string would be split into characters)"
            )
        restricted_zones[zone_id] = RestrictedZonePolicy(
            min_security_level=int(zone_cfg.get("min_security_level", 1)),
            authorized_person_ids=list(authorized),
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

    raw_corr = data.get("correlation", {})
    if isinstance(raw_corr, dict):
        correlation = CorrelationPolicy(
            badge_entry_window_seconds=float(raw_corr.get("badge_entry_window_seconds", 15.0)),
            tailgating_window_seconds=float(raw_corr.get("tailgating_window_seconds", 10.0)),
            transit_slack_seconds=float(raw_corr.get("transit_slack_seconds", 2.0)),
            grant_after_entry_tolerance_seconds=float(raw_corr.get("grant_after_entry_tolerance_seconds", 0.0)),
        )
    else:
        correlation = CorrelationPolicy()

    return AccessPolicy(
        version=version,
        restricted_zones=restricted_zones,
        business_hours_utc=business_hours,
        dwell_alert_min_security_level=dwell_min_sec,
        correlation=correlation,
    )
