from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from shapely.geometry import Polygon


@dataclass(frozen=True)
class PolygonValidationResult:
    """Encapsulates a verified polygon and its derived spatial metrics."""
    polygon: Polygon
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    area: float


class PolygonValidator:
    """Validates 2D planar polygon geometries prior to database persistence."""

    @staticmethod
    def validate_vertices(vertices: List[Tuple[float, float]]) -> PolygonValidationResult:
        """Validate an ordered sequence of (x, y) coordinates for a zone polygon.

        Checks:
        1. Minimum of 3 distinct vertices.
        2. Finite numeric coordinates.
        3. Geometric validity (no self-intersections or bowtie loops).
        4. Positive non-zero surface area.

        Returns:
            PolygonValidationResult with the Shapely Polygon and computed bounding box.
        Raises:
            ValueError: If the polygon fails any geometric validation rules.
        """
        if not vertices or len(vertices) < 3:
            raise ValueError(f"Polygon requires at least 3 vertices, got {len(vertices) if vertices else 0}.")

        for i, (x, y) in enumerate(vertices):
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(f"Vertex {i} contains non-finite coordinate values: ({x}, {y}).")

        # Strip redundant closing vertex if caller passed closed loop
        coords = list(vertices)
        if len(coords) > 3 and coords[0] == coords[-1]:
            coords = coords[:-1]

        if len(coords) < 3:
            raise ValueError("Polygon has fewer than 3 unique vertices after removing closing point.")

        try:
            poly = Polygon(coords)
        except Exception as e:
            raise ValueError(f"Failed to construct Shapely Polygon: {e}") from e

        if not poly.is_valid:
            from shapely.validation import explain_validity
            raise ValueError(f"Polygon geometry is invalid (self-intersecting or complex): {explain_validity(poly)}")

        if poly.area <= 0.0:
            raise ValueError(f"Polygon area must be strictly positive, got {poly.area} (collinear or zero-area).")

        min_x, min_y, max_x, max_y = poly.bounds
        return PolygonValidationResult(
            polygon=poly,
            min_x=float(min_x),
            min_y=float(min_y),
            max_x=float(max_x),
            max_y=float(max_y),
            area=float(poly.area),
        )


class EventValidator:
    """Validates twin event attributes and integrity before ledger persistence (Patch 4)."""

    ALLOWED_EVENT_TYPES: Set[str] = {
        "PERSON_DETECTED",
        "ZONE_ENTERED",
        "ZONE_EXITED",
        "CROSS_ZONE_TRANSITION",
        "DWELL_TIME_EXCEEDED",
        "AFTER_HOURS_PRESENCE",
        "UNAUTHORIZED_ACCESS",
        "TAILGATING_SUSPECTED",
        "TRACK_LOST",
        "ACCESS_GRANTED",
        "ACCESS_DENIED",
    }

    @classmethod
    def validate_event_type(cls, event_type: str) -> str:
        if not event_type or event_type not in cls.ALLOWED_EVENT_TYPES:
            raise ValueError(f"Invalid event_type '{event_type}'. Must be one of {sorted(cls.ALLOWED_EVENT_TYPES)}")
        return event_type

    @staticmethod
    def validate_track_id(track_id: str) -> str:
        stripped = track_id.strip() if track_id else ""
        if not stripped:
            raise ValueError("track_id cannot be empty or blank.")
        return stripped

    @staticmethod
    def validate_timestamp(timestamp: str) -> str:
        if not timestamp or not isinstance(timestamp, str):
            raise ValueError("timestamp must be a non-empty ISO-8601 string.")
        try:
            # Validate ISO-8601 compliance
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except Exception as e:
            raise ValueError(f"Invalid ISO-8601 timestamp '{timestamp}': {e}") from e
        return timestamp

    @staticmethod
    def validate_coordinates(coords: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
        if coords is None:
            return None
        x, y = coords
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f"Coordinates must be finite numeric values, got ({x}, {y}).")
        return (float(x), float(y))

    @staticmethod
    def validate_confidence(confidence: Optional[float]) -> Optional[float]:
        if confidence is None:
            return None
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence score must be in range [0.0, 1.0], got {confidence}.")
        return float(confidence)
