from datetime import datetime, timezone
import logging
from typing import Optional, TYPE_CHECKING

try:
    from src.twin.engine import DigitalTwinEngine
    from src.vision.calibration import HomographyManager
    from src.vision.geometry import CameraObservation, FloorPoint, PixelPoint, TwinObservation
    from src.vision.topology import TopologyService
    from src.vision.zones import VisionZoneResolver
    from src.core.locks import TWIN_WRITE_LOCK
except ImportError:
    from twin.engine import DigitalTwinEngine
    from vision.calibration import HomographyManager
    from vision.geometry import CameraObservation, FloorPoint, PixelPoint, TwinObservation
    from vision.topology import TopologyService
    from vision.zones import VisionZoneResolver
    from core.locks import TWIN_WRITE_LOCK

if TYPE_CHECKING:
    try:
        from src.rules.engine import IncidentRuleEngine
    except ImportError:
        from rules.engine import IncidentRuleEngine

logger = logging.getLogger("office_vision.vision.pipeline")


class ObservationPipeline:
    """Deterministic Vision Observation Pipeline.

    Bridges 2D camera detections into the Digital Twin Core:
    1. Ingests CameraObservation (bottom-center ground pixel contact point).
    2. Projects camera pixel coordinates (u, v) -> floor coordinates (x, y) via calibrated homography.
    3. Resolves spatial zone containment via STRtree spatial index.
    4. Ingests transformed observation directly into DigitalTwinEngine.ingest_observation().
    5. Returns structured TwinObservation with emitted ledger events and raised incidents.

    Prohibitions: Zero detector, tracker, RTSP stream, or Bedrock runtime logic. (Deterministic rule evaluation allowed).
    """

    def __init__(
        self,
        homography_manager: HomographyManager,
        zone_resolver: VisionZoneResolver,
        twin_engine: DigitalTwinEngine,
        topology_service: Optional[TopologyService] = None,
        rule_engine: Optional["IncidentRuleEngine"] = None,
    ):
        self.homography_manager = homography_manager
        self.zone_resolver = zone_resolver
        self.twin_engine = twin_engine
        self.topology_service = topology_service
        self.rule_engine = rule_engine

    def process_observation(self, observation: CameraObservation) -> TwinObservation:
        """Process a single camera observation and push state into the Digital Twin Core.

        Args:
            observation: Validated CameraObservation.

        Returns:
            TwinObservation containing transformed floor coordinates, resolved zone, and twin events.
        """
        with TWIN_WRITE_LOCK:
            pixel_pt = observation.bottom_center
            if pixel_pt is None:
                if observation.bbox is not None:
                    pixel_pt = observation.bbox.bottom_center
                else:
                    raise ValueError(
                        f"Observation for track '{observation.track_id}' on camera '{observation.camera_id}' has no pixel coordinates."
                    )

            timestamp = observation.timestamp or datetime.now(timezone.utc).isoformat()

            # 1. Project pixel to floor coordinate (metric meters, top-left origin)
            projection = self.homography_manager.project_pixel_to_floor(
                camera_id=observation.camera_id,
                pixel_point=pixel_pt,
                confidence=observation.confidence,
            )
            floor_pt = projection.floor_point

            # 2. Resolve zone via STRtree
            zone_id = self.zone_resolver.resolve_zone(
                floor_point=floor_pt,
                geometry_version=projection.geometry_version,
            )

            # 3. Ingest into Digital Twin Core
            emitted_events = self.twin_engine.ingest_observation(
                track_id=observation.track_id,
                camera_id=observation.camera_id,
                x=floor_pt.x,
                y=floor_pt.y,
                timestamp=timestamp,
                confidence=observation.confidence,
                person_id=observation.person_id,
            )

            # 4. Evaluate security incident rules
            raised_incidents = []
            if self.rule_engine is not None:
                raised_incidents = self.rule_engine.evaluate(emitted_events)

            return TwinObservation(
                track_id=observation.track_id,
                camera_id=observation.camera_id,
                floor_point=floor_pt,
                pixel_point=pixel_pt,
                zone_id=zone_id,
                emitted_events=emitted_events,
                confidence=observation.confidence,
                timestamp=timestamp,
                person_id=observation.person_id,
                raised_incidents=raised_incidents,
            )

