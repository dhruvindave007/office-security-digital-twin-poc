# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timezone
import json
import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
try:
    from src.core.locks import TWIN_WRITE_LOCK
    from src.core.timeutil import canonical_utc
    from src.rules.policy import parse_utc_timestamp
    from src.twin.history import occupancy_series, position_heatmap, track_path
except ImportError:
    from core.locks import TWIN_WRITE_LOCK
    from core.timeutil import canonical_utc
    from rules.policy import parse_utc_timestamp
    from twin.history import occupancy_series, position_heatmap, track_path

try:
    from src.twin.engine import DigitalTwinEngine
    from src.twin.models import Incident
    from src.twin.schemas import (
        EventResponse,
        EventsListResponse,
        IncidentBulkStatusRequest,
        IncidentBulkStatusResponse,
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
        IncidentBulkStatusRequest,
        IncidentBulkStatusResponse,
        IncidentPatchRequest,
        IncidentResponse,
        IncidentsListResponse,
        TrackedEntityResponse,
        TwinStateResponse,
        VertexSchema,
        ZoneResponse,
    )

router = APIRouter(prefix="/api/twin", tags=["Digital Twin"])


def _time_param(name: str, value: Optional[str]) -> Optional[str]:
    """Validate an ISO-8601 query parameter and return it in canonical ledger format (422 on garbage)."""
    if value is None or value == "":
        return None
    try:
        return canonical_utc(value)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid {name}: {e}")


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
    offset: int = Query(0, ge=0, le=10_000_000),
    zone_id: Optional[str] = None,
    track_id: Optional[str] = None,
    event_type: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    event_ids: Optional[str] = Query(None, description="Comma-separated event IDs (max 200); other filters ignored."),
):
    """Query the immutable append-only event ledger with indexed filters."""
    start_time = _time_param("start_time", start_time)
    end_time = _time_param("end_time", end_time)
    engine = _get_engine(request)
    if event_ids is not None:
        ids = [i.strip() for i in event_ids.split(",") if i.strip()]
        if len(ids) > 200:
            raise HTTPException(status_code=422, detail="event_ids: at most 200 IDs")
        events = engine.store.get_events_by_ids(ids)
        total = len(events)
    else:
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
        total=engine.store.count_incidents(status=status, severity=severity, zone_id=zone_id),
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


def _set_status(request: Request, engine: DigitalTwinEngine, incidents: List[Incident], status: str) -> List[IncidentResponse]:
    """Apply an operator status change (caller holds TWIN_WRITE_LOCK), stored in one transaction,
    and push each changed incident to every open dashboard."""
    now_iso = datetime.now(timezone.utc).isoformat()
    for inc in incidents:
        inc.updated_at = now_iso
        if status in ("RESOLVED", "FALSE_POSITIVE"):
            if inc.end_time is None:
                inc.end_time = now_iso
        else:
            inc.end_time = None  # re-opened: it has not ended
        inc.status = status

    engine.store.insert_incidents(incidents)

    # Keep the rule engine's dedupe copy identical to what was just stored; otherwise the
    # next piece of evidence overwrites this status with the engine's stale OPEN copy.
    rule_engine = getattr(request.app.state, "rule_engine", None)
    broadcaster = getattr(request.app.state, "broadcaster", None)
    views = []
    for inc in incidents:
        if rule_engine:
            rule_engine.sync_incident(inc)
        view = _row_to_incident_response(inc)
        if broadcaster is not None:
            broadcaster.publish("incident", view.model_dump())
        views.append(view)
    return views


@router.patch("/incidents/{incident_id}", response_model=IncidentResponse)
async def patch_incident(
    request: Request,
    incident_id: str,
    body: IncidentPatchRequest,
):
    """Update status of an incident."""
    with TWIN_WRITE_LOCK:
        engine = _get_engine(request)
        inc = engine.store.get_incident(incident_id)
        if not inc:
            raise HTTPException(
                status_code=404,
                detail=f"Incident {incident_id} not found",
            )
        return _set_status(request, engine, [inc], body.status)[0]


@router.post("/incidents/status", response_model=IncidentBulkStatusResponse)
async def bulk_set_status(request: Request, body: IncidentBulkStatusRequest):
    """Set one status on up to 500 incidents (e.g. resolve a burst of repeats). Unknown IDs are reported, not fatal."""
    ids = list(dict.fromkeys(body.incident_ids))   # de-duplicated, order kept
    with TWIN_WRITE_LOCK:
        engine = _get_engine(request)
        found = engine.store.get_incidents_by_ids(ids)
        updated = _set_status(request, engine, [found[i] for i in ids if i in found], body.status)
    return IncidentBulkStatusResponse(updated=updated, not_found=[i for i in ids if i not in found])


@router.get("/tracks/{track_id}/path")
async def get_track_path(
    request: Request,
    track_id: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """Retrieve ordered path points for a specific track. Returns 404 if no points exist."""
    start_time = _time_param("start_time", start_time)
    end_time = _time_param("end_time", end_time)
    engine = _get_engine(request)
    events = engine.store.get_events_ascending(
        track_id=track_id,
        event_types=["PERSON_DETECTED"],
        start_time=start_time,
        end_time=end_time,
    )
    points = track_path(events)
    if not points:
        raise HTTPException(
            status_code=404,
            detail=f"Track '{track_id}' has no path points.",
        )
    return points


@router.get("/occupancy/history")
async def get_occupancy_history(
    request: Request,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    bucket_seconds: int = Query(60, ge=1, le=86400),
    zone_id: Optional[str] = None,
):
    """Retrieve time-series occupancy buckets across zones."""
    if not start_time or not end_time:
        raise HTTPException(
            status_code=422,
            detail="start_time and end_time query parameters are required.",
        )
    try:
        start_dt = parse_utc_timestamp(start_time)
        end_dt = parse_utc_timestamp(end_time)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid timestamp format: {e}")

    if end_dt <= start_dt:
        raise HTTPException(
            status_code=422,
            detail="end_time must be strictly greater than start_time.",
        )
    if bucket_seconds < 1:
        raise HTTPException(
            status_code=422,
            detail="bucket_seconds must be at least 1.",
        )

    total_seconds = (end_dt - start_dt).total_seconds()
    num_buckets = math.ceil(total_seconds / bucket_seconds)
    if num_buckets > 1440:
        raise HTTPException(
            status_code=422,
            detail=f"Request would generate {num_buckets} buckets, exceeding the maximum limit of 1440.",
        )

    engine = _get_engine(request)
    # Occupancy at any instant depends on EVERY earlier zone event, so fetch all of them
    # (a newest-N limit silently dropped tracks that entered long ago).
    events = engine.store.get_events_ascending(
        event_types=["ZONE_ENTERED", "ZONE_EXITED", "CROSS_ZONE_TRANSITION", "TRACK_LOST"],
        end_time=canonical_utc(end_dt),
    )
    series = occupancy_series(events, start_dt, end_dt, bucket_seconds)

    if zone_id:
        filtered = []
        for item in series:
            occ = item["occupancy"]
            filtered.append(
                {
                    "bucket_start": item["bucket_start"],
                    "occupancy": {zone_id: occ.get(zone_id, 0)},
                }
            )
        return filtered

    return series


@router.get("/heatmap")
async def get_position_heatmap(
    request: Request,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    cell_size_m: float = Query(1.0),
):
    """Generate spatial 2D heatmap aggregated over PERSON_DETECTED observations."""
    if not math.isfinite(cell_size_m) or cell_size_m < 0.25 or cell_size_m > 10.0:
        raise HTTPException(
            status_code=422,
            detail="cell_size_m must be between 0.25 and 10.0.",
        )

    if start_time and end_time:
        try:
            start_dt = parse_utc_timestamp(start_time)
            end_dt = parse_utc_timestamp(end_time)
            if end_dt <= start_dt:
                raise HTTPException(
                    status_code=422,
                    detail="end_time must be strictly greater than start_time.",
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid timestamp format: {e}")

    start_time = _time_param("start_time", start_time)
    end_time = _time_param("end_time", end_time)
    engine = _get_engine(request)
    zones = engine.store.get_zones()
    width_m = max((z.max_x for z in zones), default=1.0)
    height_m = max((z.max_y for z in zones), default=1.0)

    events = engine.store.get_events_ascending(
        event_types=["PERSON_DETECTED"],
        start_time=start_time,
        end_time=end_time,
    )
    return position_heatmap(events, width_m, height_m, cell_size_m)

