"""Deterministic evidence packet for an incident investigation (no AI here).

The packet is the ONLY thing the model sees, so it must be complete enough to explain the
incident and small enough to keep cost bounded:
  - the incident and related incidents (same zone or track, same time window)
  - every ledger event of the involved tracks, plus every event in the incident zone, in the window
    (this pulls in e.g. the badge holder in a tailgating case)
  - zone/door/policy context and camera transit times
Events are capped (closest to the incident start win) and the packet says so when truncated.
"""
from datetime import timedelta
import json
from typing import Any, Dict, List, Optional, Set

try:
    from src.core.timeutil import canonical_utc, parse_utc
except ImportError:
    from core.timeutil import canonical_utc, parse_utc

WINDOW_BEFORE_S = 300
WINDOW_AFTER_S = 120
MAX_EVENTS = 150


def _event_dict(e) -> Dict[str, Any]:
    out = {"event_id": e.event_id, "type": e.event_type, "time": e.timestamp, "track_id": e.track_id}
    for key, value in (("person_id", e.person_id), ("camera_id", e.camera_id), ("zone_id", e.zone_id),
                       ("previous_zone_id", e.previous_zone_id)):
        if value:
            out[key] = value
    if e.coordinates_json:
        out["position_m"] = json.loads(e.coordinates_json)
    payload = json.loads(e.payload_json or "{}")
    if payload:
        out["payload"] = payload
    return out


def _incident_dict(i) -> Dict[str, Any]:
    return {
        "incident_id": i.incident_id, "type": i.incident_type, "severity": i.severity, "status": i.status,
        "zone_id": i.zone_id, "track_id": i.track_id, "person_id": i.person_id,
        "start_time": i.start_time, "end_time": i.end_time,
        "supporting_event_ids": json.loads(i.supporting_event_ids_json or "[]"),
        "rule_summary": i.summary,
    }


class EvidenceBuilder:
    def __init__(self, store, policy=None, access_config=None, topology=None,
                 camera_modes: Optional[Dict[str, str]] = None, position_sample_seconds: float = 0.0):
        self.store = store
        # How each camera positions people ("metric" homography or "room" level), so the model does not
        # mistake room-level placement (everyone at the room centre) for a sensor fault.
        self.camera_modes = camera_modes or {}
        self.position_sample_seconds = position_sample_seconds
        self.policy = policy
        self.access_config = access_config
        self.topology = topology

    def build(self, incident_id: str) -> Optional[Dict[str, Any]]:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            return None
        supporting: List[str] = json.loads(incident.supporting_event_ids_json or "[]")

        start = parse_utc(incident.start_time)
        anchor_end = parse_utc(incident.end_time) if incident.end_time else start
        lo = canonical_utc(start - timedelta(seconds=WINDOW_BEFORE_S))
        hi = canonical_utc(anchor_end + timedelta(seconds=WINDOW_AFTER_S))

        # Tracks involved: the incident's own + every track referenced by its supporting events.
        tracks: Set[str] = {incident.track_id} if incident.track_id else set()
        if supporting:
            for e in self.store.get_events_ascending(start_time=lo, end_time=hi):
                if e.event_id in supporting:
                    tracks.add(e.track_id)

        events: Dict[str, Any] = {}
        if tracks:
            for e in self.store.get_events_ascending(track_ids=sorted(tracks), start_time=lo, end_time=hi):
                events[e.event_id] = e
        if incident.zone_id:
            for e in self.store.get_events_ascending(zone_id=incident.zone_id, start_time=lo, end_time=hi):
                events[e.event_id] = e

        ordered = sorted(events.values(), key=lambda e: e.sequence_num or 0)
        truncated = len(ordered) > MAX_EVENTS
        if truncated:
            must_keep = [e for e in ordered if e.event_id in supporting]
            rest = sorted((e for e in ordered if e.event_id not in supporting),
                          key=lambda e: abs((parse_utc(e.timestamp) - start).total_seconds()))
            ordered = sorted(must_keep + rest[: max(0, MAX_EVENTS - len(must_keep))], key=lambda e: e.sequence_num or 0)

        involved_tracks = {e.track_id for e in ordered}
        related = [
            _incident_dict(i) for i in self.store.get_incidents(limit=500)
            if i.incident_id != incident.incident_id
            and lo <= i.start_time <= hi
            and (i.zone_id == incident.zone_id or (i.track_id and i.track_id in involved_tracks))
        ]

        inc_view = _incident_dict(incident)
        if len(supporting) >= 50:
            inc_view["supporting_event_ids_note"] = ("long-running incident: only its first 20 and latest 30 "
                                                     "supporting events are listed")
        return {
            "incident": inc_view,
            "related_incidents": related[:20],
            "events": [_event_dict(e) for e in ordered],
            "events_truncated": truncated,
            "window": {"start": lo, "end": hi},
            "context": self._context(incident.zone_id, {e.camera_id for e in ordered if e.camera_id}),
        }

    def _context(self, zone_id: Optional[str], cameras: Set[str]) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {}
        zone = self.store.get_zone(zone_id) if zone_id else None
        if zone:
            ctx["zone"] = {"zone_id": zone.zone_id, "name": zone.name, "type": zone.zone_type,
                           "security_level": zone.security_level, "max_occupancy": zone.max_occupancy}
        if self.policy is not None:
            restricted = self.policy.restricted_zones.get(zone_id) if zone_id else None
            ctx["policy"] = {
                "zone_is_restricted": restricted is not None,
                "authorized_person_ids": list(restricted.authorized_person_ids) if restricted else [],
                "business_hours_utc": dict(self.policy.business_hours_utc),
            }
        if self.access_config is not None and zone_id:
            ctx["doors"] = [{"door_id": d.door_id, "name": d.name, "badge_required": d.badge_required,
                             "camera_id": d.camera_id}
                            for d in self.access_config.doors.values() if d.zone_id == zone_id]
        if self.topology is not None and len(cameras) > 1:
            cams = sorted(cameras)
            ctx["camera_min_transit_seconds"] = {
                f"{a}->{b}": self.topology.min_transit_seconds(a, b) for a in cams for b in cams if a < b
            }
        modes = {c: self.camera_modes[c] for c in sorted(cameras) if c in self.camera_modes}
        if modes:
            ctx["camera_positioning"] = {
                c: ("metric: x,y is the person's measured floor position" if m == "metric" else
                    "room-level: this camera only tells which room a person is in; every person it sees is "
                    "placed at the room's centre point, so identical coordinates are expected, not a fault")
                for c, m in modes.items()}
        if self.position_sample_seconds > 0:
            ctx["position_sampling_note"] = (f"PERSON_DETECTED positions are stored at most once every "
                                             f"{self.position_sample_seconds:g} s per track; tracking is continuous "
                                             f"between stored samples")
        ctx["identity_note"] = "track_id and person_id are system labels, not verified identities."
        return ctx
