"""Live camera control, status, and pictures (single JPEG and MJPEG stream)."""
import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/live", tags=["Live Cameras"])

MAX_MJPEG_STREAMS = 8                      # each stream costs a JPEG encode per frame
_mjpeg_slots = asyncio.Semaphore(MAX_MJPEG_STREAMS)
_WIDTHS = (320, 480, 640, 960, 1280, 1920)


def _snap_width(width: int) -> int:
    """A few fixed sizes, so varying `width` cannot defeat the per-frame JPEG cache."""
    return min(_WIDTHS, key=lambda w: abs(w - width))


class StartRequest(BaseModel):
    config_path: Optional[str] = Field(default=None, description="Live camera YAML; default LIVE_CONFIG_PATH.")


def _manager(request: Request):
    mgr = getattr(request.app.state, "live_manager", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Live camera manager is not initialized.")
    return mgr


@router.get("/status")
def live_status(request: Request) -> Dict[str, Any]:
    return _manager(request).status()


@router.post("/start")
def live_start(request: Request, body: StartRequest = StartRequest()) -> Dict[str, Any]:
    mgr = _manager(request)
    # The camera list is server configuration (LIVE_CONFIG_PATH), never a client-chosen path: a client
    # path would let a caller make the server open any file or stream URL (SSRF / file probing).
    if body.config_path:
        raise HTTPException(status_code=400, detail="config_path is not accepted over the API; set LIVE_CONFIG_PATH on the server.")
    try:
        return mgr.start(None)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (ValueError, FileNotFoundError, OSError):
        raise HTTPException(status_code=422, detail="The server's live camera configuration could not be loaded; see the server log.")


@router.post("/stop")
def live_stop(request: Request) -> Dict[str, Any]:
    return _manager(request).stop()


@router.get("/cameras/{camera_id}/frame.jpg")
def live_frame(camera_id: str, request: Request, annotated: bool = True, width: int = 960) -> Response:
    mgr = _manager(request)
    if camera_id not in mgr.workers:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} is not running")
    data = mgr.jpeg(camera_id, annotated=annotated, max_width=_snap_width(width))
    if data is None:
        raise HTTPException(status_code=503, detail=f"No picture from {camera_id} yet")
    # The last picture is served even when the stream has stopped delivering: say how old it is, so a
    # frozen picture is never mistaken for a live one.
    headers = {"Cache-Control": "no-store", "Access-Control-Expose-Headers": "X-Frame-Captured-At, X-Frame-Age-Ms"}
    taken = mgr.picture_time(camera_id)
    if taken is not None:
        from datetime import datetime, timezone
        headers["X-Frame-Captured-At"] = taken.isoformat()
        headers["X-Frame-Age-Ms"] = str(max(0, int((datetime.now(timezone.utc) - taken).total_seconds() * 1000)))
    return Response(content=data, media_type="image/jpeg", headers=headers)


@router.get("/cameras/{camera_id}/mjpeg")
async def live_mjpeg(camera_id: str, request: Request, annotated: bool = True, width: int = 960, fps: float = 5.0):
    """Motion-JPEG stream: works directly in an <img src> tag. Sends only when the picture changed."""
    mgr = _manager(request)
    if camera_id not in mgr.workers:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} is not running")
    if _mjpeg_slots.locked():
        raise HTTPException(status_code=429, detail=f"Too many open camera streams (max {MAX_MJPEG_STREAMS}).")
    width = _snap_width(width)
    period = 1.0 / max(0.5, min(fps, 15.0))

    async def frames():
        async with _mjpeg_slots:
            last = None
            while camera_id in mgr.workers and not await request.is_disconnected():
                data = await asyncio.to_thread(mgr.jpeg, camera_id, annotated, 75, width)
                if data is not None and data is not last:
                    last = data
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                await asyncio.sleep(period)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


# -- Evidence clips (files under settings.evidence_dir; readable whether or not cameras run) --------
evidence_router = APIRouter(tags=["Evidence"])


def _evidence_root(request: Request):
    from pathlib import Path
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        try:
            from src.config import get_settings
        except ImportError:
            from config import get_settings
        settings = get_settings()
    return Path(settings.evidence_dir).resolve()


@evidence_router.get("/api/twin/incidents/{incident_id}/evidence")
def incident_evidence(incident_id: str, request: Request) -> Dict[str, Any]:
    import json
    import re
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", incident_id):
        raise HTTPException(status_code=422, detail="bad incident id")
    root = _evidence_root(request)
    path = root / "incidents" / f"{incident_id}.json"
    if not path.exists():
        return {"incident_id": incident_id, "clips": []}
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["clips"] = [dict(c, video_url=f"/api/evidence/{c['video']}", key_frame_url=f"/api/evidence/{c['key_frame']}")
                    for c in doc["clips"] if (root / c["video"]).exists()]
    return doc


@evidence_router.get("/api/evidence/clips/{name}")
def evidence_file(name: str, request: Request):
    from fastapi.responses import FileResponse
    root = _evidence_root(request) / "clips"
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():     # no path traversal, files only
        raise HTTPException(status_code=404, detail="not found")
    media = {".mp4": "video/mp4", ".avi": "video/x-msvideo", ".jpg": "image/jpeg"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)
