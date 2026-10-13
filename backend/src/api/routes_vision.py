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
    if not pipeline.homography_manager.has_camera(payload.camera_id):
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


# ---------------------------------------------------------------------------
# Clip Evaluation Routes
# ---------------------------------------------------------------------------

try:
    from src.config import get_settings
    from src.core.paths import _resolve_data_path
    from src.vision.clips import list_clip_configs, load_clip_config
    from src.vision.clip_jobs import JobConflictError, JobNotFoundError
except ImportError:
    from config import get_settings
    from core.paths import _resolve_data_path
    from vision.clips import list_clip_configs, load_clip_config
    from vision.clip_jobs import JobConflictError, JobNotFoundError


@router.get("/clips")
async def list_clips(request: Request):
    """List all configured evaluation clips and whether their video files are present on disk."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    clips_dir = _resolve_data_path(settings.vision_clips_dir, "data/clips")
    configs = list_clip_configs(clips_dir)

    results = []
    for cfg in configs:
        video_path = clips_dir / cfg.video_file
        results.append({
            "name": cfg.name,
            "description": cfg.description,
            "camera_id": cfg.camera_id,
            "video_file": cfg.video_file,
            "video_present": video_path.exists(),
            "source_note": cfg.source_note,
        })
    return results


@router.post("/clips/{name}/run", status_code=202)
async def run_clip(name: str, request: Request):
    """Trigger background evaluation run for a configured clip."""
    pipeline = getattr(request.app.state, "observation_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="Observation pipeline is not initialized.",
        )

    job_manager = getattr(request.app.state, "clip_job_manager", None)
    if job_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Clip job manager is not initialized.",
        )

    tracker_factory = getattr(request.app.state, "tracker_factory", None)
    if tracker_factory is None:
        raise HTTPException(
            status_code=503,
            detail="Tracker factory is not configured.",
        )

    settings = getattr(request.app.state, "settings", None) or get_settings()
    clips_dir = _resolve_data_path(settings.vision_clips_dir, "data/clips")

    # Find clip manifest
    cfg_path = clips_dir / f"{name}.yaml"
    if not cfg_path.exists():
        cfg_path = clips_dir / f"{name}.yml"
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail=f"Clip '{name}' not found.")

    try:
        config = load_clip_config(cfg_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid clip config '{name}': {exc}")

    # Validate video file presence
    video_path = clips_dir / config.video_file
    if not video_path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"Video file '{config.video_file}' for clip '{name}' is missing.",
        )

    try:
        run_id = job_manager.start(config=config, tracker_factory=tracker_factory)
        return {"run_id": run_id}
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/clips/runs/{run_id}")
async def get_clip_run(run_id: str, request: Request):
    """Retrieve execution status and performance metrics for a clip run."""
    job_manager = getattr(request.app.state, "clip_job_manager", None)
    if job_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Clip job manager is not initialized.",
        )

    record = job_manager.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return record


@router.post("/clips/runs/{run_id}/cancel", status_code=202)
async def cancel_clip_run(run_id: str, request: Request):
    """Cancel an active clip run."""
    job_manager = getattr(request.app.state, "clip_job_manager", None)
    if job_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Clip job manager is not initialized.",
        )

    try:
        job_manager.cancel(run_id)
        return {"run_id": run_id, "status": "cancelling"}
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

