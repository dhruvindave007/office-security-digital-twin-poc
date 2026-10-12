from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class VertexSchema(BaseModel):
    """Schema for individual zone polygon vertices."""
    model_config = ConfigDict(from_attributes=True)

    vertex_order: int
    x: float
    y: float


class ZoneResponse(BaseModel):
    """Schema for returning zone definitions, boundaries, and current occupancy."""
    model_config = ConfigDict(from_attributes=True)

    zone_id: str
    name: str
    zone_type: str
    security_level: int
    max_occupancy: Optional[int] = None
    current_occupancy: int = 0
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    geometry_version: int = 1
    vertices: List[VertexSchema] = Field(default_factory=list)


class TrackedEntityResponse(BaseModel):
    """Schema for an active entity tracked by the Digital Twin."""
    model_config = ConfigDict(from_attributes=True)

    track_id: str
    person_id: Optional[str] = None
    current_zone_id: Optional[str] = None
    current_camera_id: Optional[str] = None
    position: Optional[List[float]] = None
    first_seen: str
    last_seen: str
    dwell_seconds: float = 0.0
    confidence: float = 1.0
    status: str = "ACTIVE"


class TwinStateResponse(BaseModel):
    """Schema for the current comprehensive Digital Twin state."""
    model_config = ConfigDict(from_attributes=True)

    timestamp: str
    total_active_tracks: int
    zone_occupancy: Dict[str, int]
    active_tracks: List[TrackedEntityResponse]


class EventResponse(BaseModel):
    """Schema for an immutable event ledger record."""
    model_config = ConfigDict(from_attributes=True)

    sequence_num: int
    event_id: str
    event_type: str
    timestamp: str
    track_id: str
    person_id: Optional[str] = None
    camera_id: Optional[str] = None
    zone_id: Optional[str] = None
    previous_zone_id: Optional[str] = None
    confidence: Optional[float] = None
    coordinates: Optional[Dict[str, float]] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class EventsListResponse(BaseModel):
    """Schema for paginated event queries."""
    model_config = ConfigDict(from_attributes=True)

    total: int
    limit: int
    offset: int
    events: List[EventResponse]


class IncidentResponse(BaseModel):
    """Schema for a security incident record."""
    model_config = ConfigDict(from_attributes=True)

    incident_id: str
    incident_type: str
    severity: str
    status: str
    zone_id: Optional[str] = None
    track_id: Optional[str] = None
    person_id: Optional[str] = None
    start_time: str
    end_time: Optional[str] = None
    supporting_event_ids: List[str] = Field(default_factory=list)
    summary: str


class IncidentsListResponse(BaseModel):
    """Schema for query results of incidents."""
    model_config = ConfigDict(from_attributes=True)

    total: int
    incidents: List[IncidentResponse]


class IncidentPatchRequest(BaseModel):
    """Schema for updating incident status."""
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"OPEN", "INVESTIGATING", "RESOLVED", "FALSE_POSITIVE"}
        if v not in allowed:
            raise ValueError(f"Invalid status '{v}'. Allowed: {', '.join(sorted(allowed))}")
        return v

