"""Vision Geometry Foundation package for Office Vision Intelligence Platform.

Provides deterministic planar homography transformations, camera adjacency topology,
STRtree zone resolution, and the observation pipeline bridging camera pixel detections
into the Digital Twin Core.
"""

from src.vision.calibration import HomographyManager
from src.vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode
from src.vision.geometry import (
    BoundingBox2D,
    CameraObservation,
    FloorPoint,
    PixelPoint,
    ProjectionResult,
    TwinObservation,
)
from src.vision.pipeline import ObservationPipeline
from src.vision.reid import (
    ReIDExtractorInterface,
    ReIDMatcherInterface,
    VisualFeatureVector,
    cosine_similarity,
)
from src.vision.seed import VisionSeedLoader
from src.vision.topology import TopologyService
from src.vision.validators import (
    CalibrationValidator,
    CameraValidator,
    ProjectionValidator,
    TopologyValidator,
)
from src.vision.zones import VisionZoneResolver

__all__ = [
    "PixelPoint",
    "FloorPoint",
    "BoundingBox2D",
    "CameraObservation",
    "TwinObservation",
    "ProjectionResult",
    "HomographyManager",
    "CameraNode",
    "CameraEdge",
    "CameraAdjacencyGraph",
    "TopologyService",
    "VisionZoneResolver",
    "ObservationPipeline",
    "VisionSeedLoader",
    "CalibrationValidator",
    "CameraValidator",
    "TopologyValidator",
    "ProjectionValidator",
    "VisualFeatureVector",
    "cosine_similarity",
    "ReIDExtractorInterface",
    "ReIDMatcherInterface",
]
