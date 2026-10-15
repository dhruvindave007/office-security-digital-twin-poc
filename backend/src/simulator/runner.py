from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Dict, List, Optional

try:
    from src.access.service import AccessControlService
    from src.core.locks import TWIN_WRITE_LOCK
    from src.rules.policy import parse_utc_timestamp
    from src.simulator.scenarios import Scenario
    from src.vision.calibration import HomographyManager
    from src.vision.geometry import CameraObservation, FloorPoint
    from src.vision.pipeline import ObservationPipeline
except ImportError:
    from access.service import AccessControlService
    from core.locks import TWIN_WRITE_LOCK
    from rules.policy import parse_utc_timestamp
    from simulator.scenarios import Scenario
    from vision.calibration import HomographyManager
    from vision.geometry import CameraObservation, FloorPoint
    from vision.pipeline import ObservationPipeline

logger = logging.getLogger("office_vision.simulator.runner")


@dataclass
class ScenarioRunResult:
    """Summary of deterministic execution of a scenario."""

    scenario_name: str
    steps_processed: int
    events_emitted: int
    event_type_counts: Dict[str, int] = field(default_factory=dict)
    incidents: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ScenarioRunner:
    """Synchronous, deterministic scenario runner executing observation and access steps."""

    def __init__(
        self,
        pipeline: ObservationPipeline,
        homography_manager: HomographyManager,
        access_service: Optional[AccessControlService] = None,
    ):
        self.pipeline = pipeline
        self.homography_manager = homography_manager
        self.access_service = access_service

    def run(self, scenario: Scenario) -> ScenarioRunResult:
        """Run all steps of a scenario synchronously as fast as possible (no sleep)."""
        # "now": play the scenario at the current time (e.g. alongside live cameras, so its incidents
        # line up with live video and evidence); otherwise the fixed historical base time.
        if str(scenario.base_time_utc).strip().lower() == "now":
            base_dt = datetime.now(timezone.utc)
        else:
            base_dt = parse_utc_timestamp(scenario.base_time_utc)

        steps_processed = 0
        events_emitted = 0
        event_type_counts: Dict[str, int] = {}
        unique_incidents: List[str] = []
        errors: List[str] = []

        for step in scenario.steps:
            try:
                step_dt = base_dt + timedelta(seconds=step.t)
                timestamp = step_dt.isoformat()

                if step.kind == "access":
                    if self.access_service is None:
                        logger.warning(
                            "Cannot process access step at t=%.2f in scenario '%s': access_service is not configured.",
                            step.t,
                            scenario.name,
                        )
                        errors.append(f"Step at t={step.t}: access_service is not configured for access step")
                        continue

                    evt, raised = self.access_service.ingest(
                        door_id=step.door_id,
                        badge_id=step.badge_id,
                        result=step.result,
                        timestamp=timestamp,
                    )
                    steps_processed += 1
                    events_emitted += 1
                    event_type_counts[evt.event_type] = event_type_counts.get(evt.event_type, 0) + 1

                    for inc in raised:
                        if inc.incident_id not in unique_incidents:
                            unique_incidents.append(inc.incident_id)

                elif not self.homography_manager.has_camera(step.camera_id):
                    # Site cameras without a pixel homography here (e.g. room-level WiseNET cameras):
                    # the step already gives the floor position, so feed the twin directly.
                    twin = self.pipeline.twin_engine
                    rules = self.pipeline.rule_engine
                    with TWIN_WRITE_LOCK:
                        evs = twin.ingest_observation(
                            track_id=step.track_id, camera_id=step.camera_id, x=step.floor[0], y=step.floor[1],
                            timestamp=timestamp, confidence=step.confidence, person_id=step.person_id)
                        raised = rules.evaluate(evs) if rules is not None else []
                    steps_processed += 1
                    for evt in evs:
                        events_emitted += 1
                        event_type_counts[evt.event_type] = event_type_counts.get(evt.event_type, 0) + 1
                    for inc in raised:
                        if inc.incident_id not in unique_incidents:
                            unique_incidents.append(inc.incident_id)
                else:
                    # 1. Project floor coordinate -> camera pixel space (u, v)
                    floor_pt = FloorPoint(x=step.floor[0], y=step.floor[1])
                    pixel_pt = self.homography_manager.project_floor_to_pixel(
                        camera_id=step.camera_id,
                        floor_point=floor_pt,
                    )

                    # 2. Build CameraObservation
                    obs = CameraObservation(
                        camera_id=step.camera_id,
                        track_id=step.track_id,
                        bottom_center=pixel_pt,
                        confidence=step.confidence,
                        timestamp=timestamp,
                        person_id=step.person_id,
                    )

                    # 3. Process observation through pipeline into Twin & RuleEngine
                    res = self.pipeline.process_observation(obs)
                    steps_processed += 1

                    for evt in res.emitted_events:
                        events_emitted += 1
                        event_type_counts[evt.event_type] = event_type_counts.get(evt.event_type, 0) + 1

                    for inc in res.raised_incidents:
                        if inc.incident_id not in unique_incidents:
                            unique_incidents.append(inc.incident_id)

            except Exception as e:
                logger.warning("Error processing step in scenario '%s' at t=%.2f: %s", scenario.name, step.t, e)
                errors.append(f"Step at t={step.t}: {str(e)}")

        return ScenarioRunResult(
            scenario_name=scenario.name,
            steps_processed=steps_processed,
            events_emitted=events_emitted,
            event_type_counts=event_type_counts,
            incidents=unique_incidents,
            errors=errors,
        )
