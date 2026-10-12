from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/system", tags=["System"])


@router.get("/bedrock")
async def get_bedrock_system_status(request: Request):
    """Retrieve Amazon Bedrock readiness status and model capabilities.

    Does NOT execute billable model inferences. Returns cached readiness details
    evaluated during application startup.
    """
    readiness = getattr(request.app.state, "bedrock_readiness", None)
    if readiness is None:
        return {
            "status": "uninitialized",
            "message": "Bedrock readiness check not completed during startup.",
        }
    return readiness
