from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List

try:
    from src.vision.geometry import BoundingBox2D
except ImportError:
    from vision.geometry import BoundingBox2D


@dataclass(frozen=True)
class TrackedDetection:
    """Represents a single object detection with a local tracker ID."""
    local_track_id: int
    bbox: BoundingBox2D
    confidence: float


class PersonTracker(ABC):
    """Abstract base class for person tracking algorithms."""

    @abstractmethod
    def update(self, frame: Any) -> List[TrackedDetection]:
        """Update tracker state with a new video frame (HxWx3 BGR array) and return active tracks."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset internal tracker state between independent video clips."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier of the tracking adapter."""
        pass
