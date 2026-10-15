import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from typing import Any, Optional

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from src.ai.bedrock_client import BedrockClient
    from src.ai.model_registry import DynamicValidator
    from src.api.routes_access import router as access_router
    from src.api.routes_investigation import router as investigation_router
    from src.api.routes_live import router as live_router
    from src.api.routes_simulator import router as simulator_router
    from src.api.routes_system import router as system_router
    from src.api.routes_twin import router as twin_router
    from src.api.routes_vision import router as vision_router
    from src.config import get_settings
    from src.core.locks import TWIN_WRITE_LOCK
    from src.live.manager import LiveManager
    from src.runtime import build_runtime
except ImportError:
    from ai.bedrock_client import BedrockClient
    from ai.model_registry import DynamicValidator
    from api.routes_access import router as access_router
    from api.routes_investigation import router as investigation_router
    from api.routes_live import router as live_router
    from api.routes_simulator import router as simulator_router
    from api.routes_system import router as system_router
    from api.routes_twin import router as twin_router
    from api.routes_vision import router as vision_router
    from config import get_settings
    from core.locks import TWIN_WRITE_LOCK
    from live.manager import LiveManager
    from runtime import build_runtime

logger = logging.getLogger("office_vision.main")


def should_prune(clip_job_manager: Any) -> bool:
    """Return True if prune loop should proceed, False if a clip run is active."""
    if clip_job_manager is not None and getattr(clip_job_manager, "active_run_id", None) is not None:
        return False
    return True


def _prune_once(twin_engine, rule_engine, now_iso: str):
    """Execute a single prune cycle and evaluate lost track events against rules."""
    with TWIN_WRITE_LOCK:
        lost_events = twin_engine.prune_tracks(now_iso)
        if lost_events and rule_engine:
            return rule_engine.evaluate(lost_events)
        return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler executing non-billable Bedrock readiness validation, Digital Twin bootstrap, and vision/rules pipeline."""
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
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "boto3_version": boto3.__version__,
    }

    # 2-3. Digital Twin, rules, correlation, access control, vision pipeline and clip jobs
    runtime = build_runtime(settings)
    twin_engine = runtime.twin_engine
    app.state.twin_engine = twin_engine
    app.state.rule_engine = runtime.rule_engine
    app.state.access_service = runtime.access_service
    app.state.homography_manager = runtime.homography_manager
    app.state.observation_pipeline = runtime.observation_pipeline
    app.state.scenario_runner = runtime.scenario_runner
    app.state.clip_runner = runtime.clip_runner
    app.state.clip_job_manager = runtime.clip_job_manager
    app.state.tracker_factory = runtime.tracker_factory
    app.state.investigation_service = runtime.investigation_service
    live_manager = LiveManager(settings, twin_engine, runtime.rule_engine, access_service=runtime.access_service)
    app.state.live_manager = live_manager
    if settings.live_autostart and settings.live_config_path:
        try:
            await asyncio.to_thread(live_manager.start)
        except Exception as e:  # the API must still come up; /api/live/status shows nothing running
            logger.error(f"Live cameras failed to start from {settings.live_config_path}: {e}")

    # 4. Background prune loop
    async def prune_loop():
        while True:
            try:
                await asyncio.sleep(settings.twin_prune_interval_seconds)
                clip_manager = getattr(app.state, "clip_job_manager", None)
                if not should_prune(clip_manager):
                    continue
                now_iso = datetime.now(timezone.utc).isoformat()
                _prune_once(twin_engine, getattr(app.state, "rule_engine", None), now_iso)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in twin prune loop: {e}", exc_info=True)

    prune_task = asyncio.create_task(prune_loop())

    try:
        yield
    finally:
        prune_task.cancel()
        try:
            await prune_task
        except asyncio.CancelledError:
            pass
        await asyncio.to_thread(live_manager.stop)
        twin_engine.store.close()  # release the per-thread SQLite connections


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
app.include_router(investigation_router)
app.include_router(access_router)
app.include_router(vision_router)
app.include_router(simulator_router)
app.include_router(live_router)


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
