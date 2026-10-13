"""AI incident investigation endpoints (Amazon Bedrock, on demand, never in the vision path)."""
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from src.ai.errors import BedrockAccessDeniedError, BedrockCredentialsError, BedrockError
    from src.ai.investigation.service import BudgetExceededError, IncidentNotFoundError, to_view
except ImportError:
    from ai.errors import BedrockAccessDeniedError, BedrockCredentialsError, BedrockError
    from ai.investigation.service import BudgetExceededError, IncidentNotFoundError, to_view

router = APIRouter(prefix="/api/twin", tags=["AI Investigation"])


class InvestigateRequest(BaseModel):
    question: str = Field(default="", max_length=1000, description="Optional operator question.")


def _service(request: Request):
    service = getattr(request.app.state, "investigation_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Investigation service is not initialized.")
    return service


# Plain `def` (not async): the Bedrock call blocks for seconds, so FastAPI runs it in a worker
# thread instead of stalling the event loop.
@router.post("/incidents/{incident_id}/investigate")
def investigate_incident(incident_id: str, request: Request, body: InvestigateRequest = InvestigateRequest()) -> Dict[str, Any]:
    service = _service(request)
    try:
        return service.investigate(incident_id, body.question)
    except IncidentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    except BudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except BedrockCredentialsError as e:
        raise HTTPException(status_code=503, detail=f"AWS credentials unavailable (run scripts/aws_sso_login.py): {e}")
    except BedrockAccessDeniedError as e:
        raise HTTPException(status_code=503, detail=f"Bedrock access denied: {e}")
    except BedrockError as e:
        raise HTTPException(status_code=502, detail=f"Bedrock call failed: {e}")


@router.get("/incidents/{incident_id}/investigations")
def list_investigations(incident_id: str, request: Request) -> Dict[str, Any]:
    service = _service(request)
    if service.store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    items: List[Dict[str, Any]] = [to_view(r) for r in service.store.get_investigations(incident_id)]
    return {
        "incident_id": incident_id,
        "investigations": items,
        "spent_today_usd": round(service.spent_today_usd(), 6),
        "daily_budget_usd": service.daily_budget_usd,
    }
