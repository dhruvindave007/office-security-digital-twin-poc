import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from src.access.config import load_access_config
    from src.access.service import AccessControlService
    from src.ai.bedrock_client import BedrockClient
    from src.ai.model_registry import DynamicValidator
    from src.api.routes_access import router as access_router
    from src.api.routes_simulator import router as simulator_router
    from src.api.routes_system import router as system_router
    from src.api.routes_twin import router as twin_router
    from src.api.routes_vision import router as vision_router
    from src.config import get_settings
    from src.rules.correlation import CorrelationEngine
    from src.rules.engine import IncidentRuleEngine
    from src.rules.policy import load_access_policy
    from src.simulator.runner import ScenarioRunner
    from src.twin.bootstrap import TwinBootstrapService
    from src.vision.pipeline import ObservationPipeline
    from src.vision.seed import VisionSeedLoader
    from src.vision.zones import VisionZoneResolver
except ImportError:
    from access.config import load_access_config
    from access.service import AccessControlService
    from ai.bedrock_client import BedrockClient
    from ai.model_registry import DynamicValidator
    from api.routes_access import router as access_router
    from api.routes_simulator import router as simulator_router
    from api.routes_system import router as system_router
    from api.routes_twin import router as twin_router
    from api.routes_vision import router as vision_router
    from config import get_settings
    from rules.correlation import CorrelationEngine
    from rules.engine import IncidentRuleEngine
    from rules.policy import load_access_policy
    from simulator.runner import ScenarioRunner
    from twin.bootstrap import TwinBootstrapService
    from vision.pipeline import ObservationPipeline
    from vision.seed import VisionSeedLoader
    from vision.zones import VisionZoneResolver

logger = logging.getLogger("office_vision.main")


def _prune_once(twin_engine, rule_engine, now_iso: str):
    """Execute a single prune cycle and evaluate lost track events against rules."""
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

    # 2. Digital Twin Core bootstrap (migrations, floorplan seed, snapshot restore, replay)
    twin_engine = TwinBootstrapService.bootstrap(
        db_path=settings.database_url,
        seed_path=settings.twin_floorplan_seed_path,
        track_ttl_seconds=settings.twin_track_ttl_seconds,
        snapshot_interval=settings.twin_snapshot_interval_events,
    )
    app.state.twin_engine = twin_engine

    # 3. Vision Geometry, Deterministic Rules, and Simulator bootstrap
    app.state.homography_manager = None
    app.state.rule_engine = None
    app.state.observation_pipeline = None
    app.state.scenario_runner = None
    app.state.access_service = None

    policy = load_access_policy(settings.rules_policy_path)
    access_config = None
    try:
        access_config = load_access_config(settings.access_config_path)
    except Exception as e:
        logger.warning(f"Failed to load access config from '{settings.access_config_path}': {e}")

    try:
        homography_mgr, camera_graph, topology = VisionSeedLoader.load_all(
            settings.vision_calibration_dir,
            settings.vision_topology_dir,
        )
        zone_resolver = VisionZoneResolver.from_twin_store(twin_engine.store)

        correlation_engine = None
        if access_config and topology:
            correlation_engine = CorrelationEngine(
                policy=policy,
                access_config=access_config,
                topology=topology,
                store=twin_engine.store,
            )

        rule_engine = IncidentRuleEngine(
            twin_engine.store,
            twin_engine.state,
            policy,
            correlation=correlation_engine,
        )
        obs_pipeline = ObservationPipeline(
            homography_manager=homography_mgr,
            zone_resolver=zone_resolver,
            twin_engine=twin_engine,
            topology_service=topology,
            rule_engine=rule_engine,
        )
        access_service = None
        if access_config:
            access_service = AccessControlService(
                store=twin_engine.store,
                config=access_config,
                rule_engine=rule_engine,
            )

        scenario_runner = ScenarioRunner(
            obs_pipeline,
            homography_mgr,
            access_service=access_service,
        )

        app.state.homography_manager = homography_mgr
        app.state.rule_engine = rule_engine
        app.state.observation_pipeline = obs_pipeline
        app.state.scenario_runner = scenario_runner
        app.state.access_service = access_service
    except Exception as e:
        logger.warning(
            f"Failed to initialize vision or topology; initializing rule engine without correlation: {e}",
            exc_info=True,
        )
        rule_engine = IncidentRuleEngine(
            twin_engine.store,
            twin_engine.state,
            policy,
            correlation=None,
        )
        app.state.rule_engine = rule_engine
        if access_config:
            try:
                app.state.access_service = AccessControlService(
                    store=twin_engine.store,
                    config=access_config,
                    rule_engine=rule_engine,
                )
            except Exception as e2:
                logger.warning(f"Failed to initialize access control service: {e2}")

    # 4. Background prune loop
    async def prune_loop():
        while True:
            try:
                await asyncio.sleep(settings.twin_prune_interval_seconds)
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
app.include_router(access_router)
app.include_router(vision_router)
app.include_router(simulator_router)


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
