import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from src.access.service import AccessControlService
except ImportError:
    from access.service import AccessControlService

logger = logging.getLogger("office_vision.api.access")

router = APIRouter(prefix="/api/access", tags=["Access Control"])


class DoorResponse(BaseModel):
    door_id: str
    name: str
    zone_id: str
    camera_id: str
    badge_required: bool


class AccessEventRequest(BaseModel):
    door_id: str
    badge_id: str
    result: str
    timestamp: str


class AccessEventResponse(BaseModel):
    event_id: str
    sequence_num: int
    person_id: Optional[str] = None
    raised_incident_ids: List[str] = Field(default_factory=list)


def _get_access_service(request: Request) -> AccessControlService:
    service = getattr(request.app.state, "access_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Access control service is not initialized.",
        )
    return service


@router.get("/doors", response_model=List[DoorResponse])
async def list_doors(request: Request):
    """Retrieve list of configured access-control doors."""
    service = _get_access_service(request)
    return [
        DoorResponse(
            door_id=d.door_id,
            name=d.name,
            zone_id=d.zone_id,
            camera_id=d.camera_id,
            badge_required=d.badge_required,
        )
        for d in service.config.doors.values()
    ]


@router.post("/events", response_model=AccessEventResponse)
async def record_access_event(request: Request, body: AccessEventRequest):
    """Ingest a physical access badge swipe event."""
    service = _get_access_service(request)
    try:
        evt, incidents = service.ingest(
            door_id=body.door_id,
            badge_id=body.badge_id,
            result=body.result,
            timestamp=body.timestamp,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return AccessEventResponse(
        event_id=evt.event_id,
        sequence_num=evt.sequence_num,
        person_id=evt.person_id,
        raised_incident_ids=[inc.incident_id for inc in incidents],
    )
