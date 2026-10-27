# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ZoneVertex:
    """Represents a vertex point defining a zone boundary polygon."""
    zone_id: str
    vertex_order: int
    x: float
    y: float
    vertex_id: Optional[int] = None


@dataclass
class Zone:
    """Represents a logical security zone within the facility floorplan."""
    zone_id: str
    name: str
    zone_type: str  # 'public', 'restricted', 'secure', 'workspace', 'corridor', 'egress'
    security_level: int = 1
    max_occupancy: Optional[int] = None
    min_x: float = 0.0  # Patch 2: Pre-computed bounding box
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    geometry_version: int = 1  # Patch 1: Geometry versioning
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Camera:
    """Represents a CCTV sensor mapped to a primary zone in the floorplan."""
    camera_id: str
    name: str
    primary_zone_id: Optional[str] = None
    stream_url: Optional[str] = None
    location_description: Optional[str] = None
    status: str = "active"
    fov_polygon_json: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class TwinEvent:
    """Immutable ledger entry representing a spatial observation or state transition."""
    event_id: str  # UUIDv7 string
    event_type: str
    timestamp: str  # ISO-8601 UTC string
    track_id: str
    sequence_num: Optional[int] = None  # Populated upon SQLite INSERT
    person_id: Optional[str] = None
    camera_id: Optional[str] = None
    zone_id: Optional[str] = None
    previous_zone_id: Optional[str] = None
    confidence: Optional[float] = None
    coordinates_json: Optional[str] = None
    payload_json: str = "{}"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Incident:
    """Structured security incident record with supporting event evidence."""
    incident_id: str
    incident_type: str
    severity: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    status: str = "OPEN"  # 'OPEN', 'INVESTIGATING', 'RESOLVED', 'FALSE_POSITIVE'
    zone_id: Optional[str] = None
    track_id: Optional[str] = None
    person_id: Optional[str] = None
    start_time: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    end_time: Optional[str] = None
    supporting_event_ids_json: str = "[]"
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class TrackedEntity:
    """In-memory active track representation maintained in the TwinState registry."""
    track_id: str
    person_id: Optional[str] = None
    current_zone_id: Optional[str] = None
    current_camera_id: Optional[str] = None
    position: Optional[Tuple[float, float]] = None
    first_seen: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_seen: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    dwell_start: Optional[str] = None
    confidence: float = 1.0
    status: str = "ACTIVE"  # 'ACTIVE', 'LOST', 'EXITED'


@dataclass
class TwinStateSnapshot:
    """Periodic serialized checkpoint of the in-memory entity registry."""
    snapshot_id: str
    sequence_num: int
    timestamp: str
    state_json: str
    checksum: str  # Patch 3: SHA-256 integrity hash of state_json
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
