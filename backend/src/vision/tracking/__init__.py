"""Vision tracking package providing tracker abstractions and test doubles."""

try:
    from src.vision.tracking.base import PersonTracker, TrackedDetection
    from src.vision.tracking.fake import ScriptedTracker
except ImportError:
    from vision.tracking.base import PersonTracker, TrackedDetection
    from vision.tracking.fake import ScriptedTracker

__all__ = ["TrackedDetection", "PersonTracker", "ScriptedTracker"]
