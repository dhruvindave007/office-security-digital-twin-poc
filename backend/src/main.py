from contextlib import asynccontextmanager
from datetime import datetime

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from ai.bedrock_client import BedrockClient
    from ai.model_registry import DynamicValidator
    from api.routes_system import router as system_router
    from api.routes_twin import router as twin_router
    from config import get_settings
    from twin.bootstrap import TwinBootstrapService
except ImportError:
    from src.ai.bedrock_client import BedrockClient
    from src.ai.model_registry import DynamicValidator
    from src.api.routes_system import router as system_router
    from src.api.routes_twin import router as twin_router
    from src.config import get_settings
    from src.twin.bootstrap import TwinBootstrapService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler executing non-billable Bedrock readiness validation and Digital Twin bootstrap."""
    settings = get_settings()

    # 1. Non-billable Bedrock readiness inspection
    validator = DynamicValidator(region_name=settings.aws_region)
    client = BedrockClient(
        region_name=settings.aws_region,
        default_model_id=settings.bedrock_reasoning_model_id,
        validator=validator,
    )
    is_cred, cred_info = client.is_credentialed()
    caps = validator.validate_and_cache(settings.bedrock_reasoning_model_id)

    app.state.bedrock_readiness = {
        "status": "ready" if is_cred else "uncredentialed",
        "configured_region": settings.aws_region,
        "reasoning_model_id": settings.bedrock_reasoning_model_id,
        "streaming_supported": caps.supports_streaming,
        "multimodal_supported": caps.supports_multimodal,
        "validation_source": caps.source,
        "credential_status": cred_info,
        "verified_at": datetime.utcnow().isoformat(),
        "boto3_version": boto3.__version__,
    }

    # 2. Digital Twin Core bootstrap (migrations, floorplan seed, snapshot restore, replay)
    twin_engine = TwinBootstrapService.bootstrap(
        db_path=settings.database_url,
        seed_path=settings.twin_floorplan_seed_path,
        track_ttl_seconds=settings.twin_track_ttl_seconds,
        snapshot_interval=settings.twin_snapshot_interval_events,
    )
    app.state.twin_engine = twin_engine

    yield


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_router)
app.include_router(twin_router)


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "service": "office-vision-backend",
        "version": "0.1.0",
        "environment": settings.app_env,
    }


@app.get("/api/health", tags=["System"])
async def api_health_check():
    return await health_check()
