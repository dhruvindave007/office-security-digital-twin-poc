from typing import Any, Dict, List

try:
    from src.vision.tracking.base import PersonTracker, TrackedDetection
except ImportError:
    from vision.tracking.base import PersonTracker, TrackedDetection


class ScriptedTracker(PersonTracker):
    """Deterministic tracker test double that plays back predefined detections per frame index."""

    def __init__(self, script: Dict[int, List[TrackedDetection]]):
        self.script = script
        self._call_index: int = 0

    def update(self, frame: Any) -> List[TrackedDetection]:
        """Return scripted detections for the current call index and increment the index."""
        detections = self.script.get(self._call_index, [])
        self._call_index += 1
        return detections

    def reset(self) -> None:
        """Reset call index counter back to 0."""
        self._call_index = 0

    @property
    def name(self) -> str:
        return "scripted"
