"""Shared assembly of the Digital Twin runtime (twin, rules, vision pipeline, access, clips).

Used by both the FastAPI lifespan (main.py) and the vision CLI (vision/cli.py) so the two can
never drift apart. Must not import cv2, torch or ultralytics at module level.
"""
from dataclasses import dataclass
import logging
from typing import Any, Callable, Dict, Optional

try:
    from src.access.config import load_access_config
    from src.access.service import AccessControlService
    from src.core.paths import _resolve_data_path
    from src.rules.correlation import CorrelationEngine
    from src.rules.engine import IncidentRuleEngine
    from src.rules.policy import load_access_policy
    from src.simulator.runner import ScenarioRunner
    from src.twin.bootstrap import TwinBootstrapService
    from src.twin.engine import DigitalTwinEngine
    from src.vision.camera_graph import CameraNode
    from src.vision.clip_jobs import ClipJobManager
    from src.vision.clip_runner import VideoClipRunner
    from src.vision.pipeline import ObservationPipeline
    from src.vision.seed import VisionSeedLoader
    from src.vision.zones import VisionZoneResolver
except ImportError:
    from access.config import load_access_config
    from access.service import AccessControlService
    from core.paths import _resolve_data_path
    from rules.correlation import CorrelationEngine
    from rules.engine import IncidentRuleEngine
    from rules.policy import load_access_policy
    from simulator.runner import ScenarioRunner
    from twin.bootstrap import TwinBootstrapService
    from twin.engine import DigitalTwinEngine
    from vision.camera_graph import CameraNode
    from vision.clip_jobs import ClipJobManager
    from vision.clip_runner import VideoClipRunner
    from vision.pipeline import ObservationPipeline
    from vision.seed import VisionSeedLoader
    from vision.zones import VisionZoneResolver

logger = logging.getLogger("office_vision.runtime")


@dataclass
class Runtime:
    """Assembled runtime components. Vision members are None when vision/topology failed to load."""

    twin_engine: DigitalTwinEngine
    rule_engine: IncidentRuleEngine
    access_service: Optional[AccessControlService] = None
    homography_manager: Any = None
    observation_pipeline: Optional[ObservationPipeline] = None
    scenario_runner: Optional[ScenarioRunner] = None
    camera_nodes: Optional[Dict[str, CameraNode]] = None
    clip_runner: Optional[VideoClipRunner] = None
    clip_job_manager: Optional[ClipJobManager] = None
    tracker_factory: Optional[Callable[[], Any]] = None


def make_tracker(settings: Any, model: Optional[str] = None, device: Optional[str] = None):
    """Build the real Ultralytics tracker from settings. Imports ultralytics lazily (inside the adapter)."""
    try:
        from src.vision.tracking.ultralytics_tracker import UltralyticsPersonTracker
    except ImportError:
        from vision.tracking.ultralytics_tracker import UltralyticsPersonTracker
    return UltralyticsPersonTracker(
        model=model or settings.vision_model,
        device=device or settings.vision_device,
        tracker_cfg=settings.vision_tracker,
        min_confidence=settings.vision_min_confidence,
        models_dir=settings.vision_models_dir,
    )


def build_runtime(settings: Any) -> Runtime:
    """Bootstrap the twin and wire rules, correlation, access control, vision pipeline and clip jobs."""
    twin_engine = TwinBootstrapService.bootstrap(
        db_path=settings.database_url,
        seed_path=settings.twin_floorplan_seed_path,
        track_ttl_seconds=settings.twin_track_ttl_seconds,
        snapshot_interval=settings.twin_snapshot_interval_events,
    )

    policy = load_access_policy(settings.rules_policy_path)
    access_config = None
    try:
        access_config = load_access_config(settings.access_config_path)
    except Exception as e:
        logger.warning(f"Failed to load access config from '{settings.access_config_path}': {e}")

    try:
        homography_mgr, _camera_graph, topology = VisionSeedLoader.load_all(
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

        camera_nodes_list = VisionSeedLoader.load_cameras(calibration_dir=settings.vision_calibration_dir)
        camera_nodes = {c.camera_id: c for c in camera_nodes_list}
        clips_dir = str(_resolve_data_path(settings.vision_clips_dir, "data/clips"))

        clip_runner = VideoClipRunner(
            pipeline=obs_pipeline,
            camera_nodes=camera_nodes,
            clips_dir=clips_dir,
        )

        return Runtime(
            twin_engine=twin_engine,
            rule_engine=rule_engine,
            access_service=access_service,
            homography_manager=homography_mgr,
            observation_pipeline=obs_pipeline,
            scenario_runner=scenario_runner,
            camera_nodes=camera_nodes,
            clip_runner=clip_runner,
            clip_job_manager=ClipJobManager(runner=clip_runner),
            tracker_factory=lambda: make_tracker(settings),
        )
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
        access_service = None
        if access_config:
            try:
                access_service = AccessControlService(
                    store=twin_engine.store,
                    config=access_config,
                    rule_engine=rule_engine,
                )
            except Exception as e2:
                logger.warning(f"Failed to initialize access control service: {e2}")
        return Runtime(twin_engine=twin_engine, rule_engine=rule_engine, access_service=access_service)
