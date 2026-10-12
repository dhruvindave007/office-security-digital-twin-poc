from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
try:
    from src.twin.engine import DigitalTwinEngine
    from src.twin.models import Incident
    from src.twin.schemas import (
        EventResponse,
        EventsListResponse,
        IncidentPatchRequest,
        IncidentResponse,
        IncidentsListResponse,
        TrackedEntityResponse,
        TwinStateResponse,
        VertexSchema,
        ZoneResponse,
    )
except ImportError:
    from twin.engine import DigitalTwinEngine
    from twin.models import Incident
    from twin.schemas import (
        EventResponse,
        EventsListResponse,
        IncidentPatchRequest,
        IncidentResponse,
        IncidentsListResponse,
        TrackedEntityResponse,
        TwinStateResponse,
        VertexSchema,
        ZoneResponse,
    )

router = APIRouter(prefix="/api/twin", tags=["Digital Twin"])


def _get_engine(request: Request) -> DigitalTwinEngine:
    engine = getattr(request.app.state, "twin_engine", None)
    if not engine:
        raise HTTPException(
            status_code=503,
            detail="Digital Twin Engine is not initialized or still bootstrapping.",
        )
    return engine


def _row_to_incident_response(inc: Incident) -> IncidentResponse:
    sup_ids = []
    if inc.supporting_event_ids_json:
        try:
            sup_ids = json.loads(inc.supporting_event_ids_json)
        except Exception:
            pass

    return IncidentResponse(
        incident_id=inc.incident_id,
        incident_type=inc.incident_type,
        severity=inc.severity,
        status=inc.status,
        zone_id=inc.zone_id,
        track_id=inc.track_id,
        person_id=inc.person_id,
        start_time=inc.start_time,
        end_time=inc.end_time,
        supporting_event_ids=sup_ids,
        summary=inc.summary,
    )


@router.get("/state", response_model=TwinStateResponse)
async def get_twin_state(request: Request):
    """Retrieve current in-memory Digital Twin state with active tracks and zone occupancies."""
    engine = _get_engine(request)
    now_dt = datetime.now(timezone.utc)
    active_tracks = engine.state.get_active_tracks()

    track_responses: List[TrackedEntityResponse] = []
    for t in active_tracks:
        dwell_sec = 0.0
        if t.dwell_start:
            try:
                start_dt = datetime.fromisoformat(t.dwell_start.replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                dwell_sec = max(0.0, (now_dt - start_dt).total_seconds())
            except Exception:
                dwell_sec = 0.0

        track_responses.append(
            TrackedEntityResponse(
                track_id=t.track_id,
                person_id=t.person_id,
                current_zone_id=t.current_zone_id,
                current_camera_id=t.current_camera_id,
                position=list(t.position) if t.position else None,
                first_seen=t.first_seen,
                last_seen=t.last_seen,
                dwell_seconds=round(dwell_sec, 1),
                confidence=t.confidence,
                status=t.status,
            )
        )

    occupancies = engine.state.get_all_zone_occupancies()
    return TwinStateResponse(
        timestamp=now_dt.isoformat(),
        total_active_tracks=len(active_tracks),
        zone_occupancy=occupancies,
        active_tracks=track_responses,
    )


@router.get("/zones", response_model=List[ZoneResponse])
async def get_zones(request: Request):
    """Retrieve all defined security zones with boundaries and live occupancy."""
    engine = _get_engine(request)
    zones = engine.store.get_zones()
    vertices_by_zone = engine.store.get_all_vertices_by_zone()

    responses: List[ZoneResponse] = []
    for z in zones:
        raw_v = vertices_by_zone.get(z.zone_id, [])
        v_schemas = [
            VertexSchema(vertex_order=v.vertex_order, x=v.x, y=v.y)
            for v in raw_v
        ]
        curr_occ = engine.state.get_zone_occupancy(z.zone_id)
        responses.append(
            ZoneResponse(
                zone_id=z.zone_id,
                name=z.name,
                zone_type=z.zone_type,
                security_level=z.security_level,
                max_occupancy=z.max_occupancy,
                current_occupancy=curr_occ,
                min_x=z.min_x,
                min_y=z.min_y,
                max_x=z.max_x,
                max_y=z.max_y,
                geometry_version=z.geometry_version,
                vertices=v_schemas,
            )
        )
    return responses


@router.get("/events", response_model=EventsListResponse)
async def get_events(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    zone_id: Optional[str] = None,
    track_id: Optional[str] = None,
    event_type: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """Query the immutable append-only event ledger with indexed filters."""
    engine = _get_engine(request)
    events, total = engine.store.get_events(
        limit=limit,
        offset=offset,
        zone_id=zone_id,
        track_id=track_id,
        event_type=event_type,
        start_time=start_time,
        end_time=end_time,
    )

    event_responses: List[EventResponse] = []
    for e in events:
        coords = None
        if e.coordinates_json:
            try:
                coords = json.loads(e.coordinates_json)
            except Exception:
                pass

        payload = {}
        if e.payload_json:
            try:
                payload = json.loads(e.payload_json)
            except Exception:
                pass

        event_responses.append(
            EventResponse(
                sequence_num=e.sequence_num or 0,
                event_id=e.event_id,
                event_type=e.event_type,
                timestamp=e.timestamp,
                track_id=e.track_id,
                person_id=e.person_id,
                camera_id=e.camera_id,
                zone_id=e.zone_id,
                previous_zone_id=e.previous_zone_id,
                confidence=e.confidence,
                coordinates=coords,
                payload=payload,
            )
        )

    return EventsListResponse(
        total=total,
        limit=limit,
        offset=offset,
        events=event_responses,
    )


@router.get("/incidents", response_model=IncidentsListResponse)
async def get_incidents(
    request: Request,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    zone_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    """Retrieve security incidents with optional status and severity filtering."""
    engine = _get_engine(request)
    incidents = engine.store.get_incidents(
        status=status,
        severity=severity,
        zone_id=zone_id,
        limit=limit,
    )

    incident_responses = [_row_to_incident_response(inc) for inc in incidents]

    return IncidentsListResponse(
        total=len(incident_responses),
        incidents=incident_responses,
    )


@router.get("/incidents/{incident_id}", response_model=IncidentResponse)
async def get_incident(request: Request, incident_id: str):
    """Retrieve a single incident by ID."""
    engine = _get_engine(request)
    inc = engine.store.get_incident(incident_id)
    if not inc:
        raise HTTPException(
            status_code=404,
            detail=f"Incident {incident_id} not found",
        )
    return _row_to_incident_response(inc)


@router.patch("/incidents/{incident_id}", response_model=IncidentResponse)
async def patch_incident(
    request: Request,
    incident_id: str,
    body: IncidentPatchRequest,
):
    """Update status of an incident."""
    engine = _get_engine(request)
    inc = engine.store.get_incident(incident_id)
    if not inc:
        raise HTTPException(
            status_code=404,
            detail=f"Incident {incident_id} not found",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    inc.updated_at = now_iso
    if body.status in ("RESOLVED", "FALSE_POSITIVE") and inc.end_time is None:
        inc.end_time = now_iso
    inc.status = body.status

    engine.store.insert_incident(inc)

    rule_engine = getattr(request.app.state, "rule_engine", None)
    if rule_engine and body.status in ("RESOLVED", "FALSE_POSITIVE"):
        rule_engine.forget(incident_id)

    return _row_to_incident_response(inc)
