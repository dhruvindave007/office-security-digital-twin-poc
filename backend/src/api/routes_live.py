"""Live camera control, status, and pictures (single JPEG and MJPEG stream)."""
import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/live", tags=["Live Cameras"])


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
    try:
        return mgr.start(body.config_path)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/stop")
def live_stop(request: Request) -> Dict[str, Any]:
    return _manager(request).stop()


@router.get("/cameras/{camera_id}/frame.jpg")
def live_frame(camera_id: str, request: Request, annotated: bool = True, width: int = 960) -> Response:
    mgr = _manager(request)
    if camera_id not in mgr.workers:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} is not running")
    data = mgr.jpeg(camera_id, annotated=annotated, max_width=max(160, min(width, 1920)))
    if data is None:
        raise HTTPException(status_code=503, detail=f"No picture from {camera_id} yet")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/cameras/{camera_id}/mjpeg")
async def live_mjpeg(camera_id: str, request: Request, annotated: bool = True, width: int = 960, fps: float = 5.0):
    """Motion-JPEG stream: works directly in an <img src> tag. Sends only when the picture changed."""
    mgr = _manager(request)
    if camera_id not in mgr.workers:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} is not running")
    width = max(160, min(width, 1920))
    period = 1.0 / max(0.5, min(fps, 15.0))

    async def frames():
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
