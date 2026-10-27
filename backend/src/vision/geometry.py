# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from src.twin.models import Incident, TwinEvent
except ImportError:
    from twin.models import Incident, TwinEvent


@dataclass(frozen=True)
class PixelPoint:
    """Represents a 2D subpixel coordinate in camera image space (u=horizontal, v=vertical)."""
    u: float
    v: float

    def __post_init__(self):
        if not math.isfinite(self.u) or not math.isfinite(self.v):
            raise ValueError(f"PixelPoint coordinates must be finite numbers, got ({self.u}, {self.v}).")
        if self.u < 0.0 or self.v < 0.0:
            raise ValueError(f"PixelPoint coordinates cannot be negative, got ({self.u}, {self.v}).")

    def to_tuple(self) -> Tuple[float, float]:
        return (self.u, self.v)

    def to_dict(self) -> Dict[str, float]:
        return {"u": round(self.u, 2), "v": round(self.v, 2)}


@dataclass(frozen=True)
class FloorPoint:
    """Represents a 2D point on the canonical office floorplan (metric meters, top-left origin)."""
    x: float
    y: float

    def __post_init__(self):
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError(f"FloorPoint coordinates must be finite numbers, got ({self.x}, {self.y}).")

    def distance_to(self, other: "FloorPoint") -> float:
        """Calculate Euclidean distance in meters to another floor point."""
        return math.hypot(self.x - other.x, self.y - other.y)

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)

    def to_dict(self) -> Dict[str, float]:
        return {"x": round(self.x, 3), "y": round(self.y, 3)}


@dataclass(frozen=True)
class BoundingBox2D:
    """Represents a 2D bounding box in camera image coordinates (u_min, v_min, u_max, v_max)."""
    u_min: float
    v_min: float
    u_max: float
    v_max: float

    def __post_init__(self):
        for val in (self.u_min, self.v_min, self.u_max, self.v_max):
            if not math.isfinite(val):
                raise ValueError("BoundingBox2D coordinates must be finite.")
        if self.u_max < self.u_min:
            raise ValueError(f"u_max ({self.u_max}) must be >= u_min ({self.u_min}).")
        if self.v_max < self.v_min:
            raise ValueError(f"v_max ({self.v_max}) must be >= v_min ({self.v_min}).")
        if self.u_min < 0.0 or self.v_min < 0.0:
            raise ValueError("BoundingBox2D coordinates cannot be negative.")

    @property
    def width(self) -> float:
        return self.u_max - self.u_min

    @property
    def height(self) -> float:
        return self.v_max - self.v_min

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def bottom_center(self) -> PixelPoint:
        """Ground contact point of the tracked subject (feet contact)."""
        center_u = (self.u_min + self.u_max) / 2.0
        return PixelPoint(u=center_u, v=self.v_max)


@dataclass(frozen=True)
class ProjectionResult:
    """Encapsulates the forward projection from camera pixel space to floor coordinates."""
    floor_point: FloorPoint
    pixel_point: PixelPoint
    camera_id: str
    confidence: float = 1.0
    geometry_version: int = 1


@dataclass(frozen=True)
class CameraObservation:
    """Input observation from a camera detection/tracking stream."""
    camera_id: str
    track_id: str
    bottom_center: Optional[PixelPoint] = None
    bbox: Optional[BoundingBox2D] = None
    confidence: float = 1.0
    timestamp: Optional[str] = None
    person_id: Optional[str] = None

    def __post_init__(self):
        if not self.camera_id or not self.camera_id.strip():
            raise ValueError("CameraObservation camera_id cannot be empty.")
        if not self.track_id or not self.track_id.strip():
            raise ValueError("CameraObservation track_id cannot be empty.")
        if self.bottom_center is None and self.bbox is None:
            raise ValueError("CameraObservation requires either bottom_center or bbox.")
        if self.bottom_center is None and self.bbox is not None:
            # Derive bottom_center from bbox
            object.__setattr__(self, "bottom_center", self.bbox.bottom_center)


@dataclass(frozen=True)
class TwinObservation:
    """Result of transforming a CameraObservation through the Vision Pipeline into the Twin Core."""
    track_id: str
    camera_id: str
    floor_point: FloorPoint
    pixel_point: PixelPoint
    zone_id: Optional[str]
    emitted_events: List[TwinEvent]
    confidence: float
    timestamp: str
    person_id: Optional[str] = None
    raised_incidents: List[Incident] = field(default_factory=list)
