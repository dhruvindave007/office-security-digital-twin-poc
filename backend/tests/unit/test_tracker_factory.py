# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from fastapi.testclient import TestClient

import src.vision.tracking.ultralytics_tracker as ultralytics_tracker_module
from src.config import get_settings
from src.main import app


class _RecordingTracker:
    """Stub standing in for UltralyticsPersonTracker; records constructor kwargs, loads nothing."""

    last_kwargs = None

    def __init__(self, **kwargs):
        _RecordingTracker.last_kwargs = kwargs


def test_default_tracker_factory_passes_real_settings(monkeypatch):
    """The REAL default factory must map existing settings onto the real constructor arguments.

    Every other clip test replaces app.state.tracker_factory, which is how a factory referencing
    non-existent settings shipped once. This test exercises the default factory itself.
    """
    monkeypatch.setattr(ultralytics_tracker_module, "UltralyticsPersonTracker", _RecordingTracker)

    with TestClient(app):
        factory = app.state.tracker_factory
        assert factory is not None
        tracker = factory()

    settings = get_settings()
    assert isinstance(tracker, _RecordingTracker)
    assert _RecordingTracker.last_kwargs == {
        "model": settings.vision_model,
        "device": settings.vision_device,
        "tracker_cfg": settings.vision_tracker,
        "min_confidence": settings.vision_min_confidence,
        "models_dir": settings.vision_models_dir,
    }


def test_should_prune():
    """Verify should_prune helper returns False when active_run_id is set, True otherwise."""
    from src.main import should_prune

    assert should_prune(None) is True

    class MockJobManager:
        def __init__(self, active_run_id=None):
            self.active_run_id = active_run_id

    assert should_prune(MockJobManager(None)) is True
    assert should_prune(MockJobManager("run_abc123")) is False

