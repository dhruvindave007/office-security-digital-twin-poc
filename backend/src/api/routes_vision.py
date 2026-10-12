import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from src.vision.geometry import BoundingBox2D, CameraObservation, PixelPoint
except ImportError:
    from vision.geometry import BoundingBox2D, CameraObservation, PixelPoint

logger = logging.getLogger("office_vision.api.vision")

router = APIRouter(prefix="/api/vision", tags=["Vision Ingest"])


class ObservationIngestRequest(BaseModel):
    camera_id: str
    track_id: str
    bottom_center: Optional[Dict[str, float]] = None
    bbox: Optional[Dict[str, float]] = None
    confidence: float = 1.0
    timestamp: Optional[str] = None
    person_id: Optional[str] = None


class ObservationIngestResponse(BaseModel):
    track_id: str
    camera_id: str
    zone_id: Optional[str] = None
    floor_point: Dict[str, float]
    emitted_event_ids: List[str] = Field(default_factory=list)
    raised_incident_ids: List[str] = Field(default_factory=list)


@router.post("/observations", response_model=ObservationIngestResponse)
async def ingest_observation(payload: ObservationIngestRequest, request: Request):
    """Ingest camera observation, project to floor plane, and evaluate against rules."""
    pipeline = getattr(request.app.state, "observation_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="Observation pipeline is not initialized.",
        )

    # Validate camera registration
    if payload.camera_id not in pipeline.homography_manager.registered_cameras:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{payload.camera_id}' not found or not calibrated.",
        )

    # Convert coordinates to domain objects
    pixel_point: Optional[PixelPoint] = None
    if payload.bottom_center:
        try:
            pixel_point = PixelPoint(
                u=float(payload.bottom_center["u"]),
                v=float(payload.bottom_center["v"]),
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid bottom_center coordinates: {e}")

    bbox_obj: Optional[BoundingBox2D] = None
    if payload.bbox:
        try:
            bbox_obj = BoundingBox2D(
                u_min=float(payload.bbox["u_min"]),
                v_min=float(payload.bbox["v_min"]),
                u_max=float(payload.bbox["u_max"]),
                v_max=float(payload.bbox["v_max"]),
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid bbox coordinates: {e}")

    if pixel_point is None and bbox_obj is None:
        raise HTTPException(
            status_code=422,
            detail="Observation requires either bottom_center or bbox.",
        )

    try:
        obs = CameraObservation(
            camera_id=payload.camera_id,
            track_id=payload.track_id,
            bottom_center=pixel_point,
            bbox=bbox_obj,
            confidence=payload.confidence,
            timestamp=payload.timestamp,
            person_id=payload.person_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        res = pipeline.process_observation(obs)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Error processing observation: %s", e)
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

    return ObservationIngestResponse(
        track_id=res.track_id,
        camera_id=res.camera_id,
        zone_id=res.zone_id,
        floor_point={"x": round(res.floor_point.x, 3), "y": round(res.floor_point.y, 3)},
        emitted_event_ids=[evt.event_id for evt in res.emitted_events],
        raised_incident_ids=[inc.incident_id for inc in res.raised_incidents],
    )
