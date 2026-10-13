import sys
from unittest.mock import MagicMock, patch
import pytest

try:
    from src.vision.geometry import BoundingBox2D
    from src.vision.tracking import PersonTracker, ScriptedTracker, TrackedDetection
    from src.vision.tracking.ultralytics_tracker import UltralyticsPersonTracker
except ImportError:
    from vision.geometry import BoundingBox2D
    from vision.tracking import PersonTracker, ScriptedTracker, TrackedDetection
    from vision.tracking.ultralytics_tracker import UltralyticsPersonTracker


def test_scripted_tracker():
    """Verify ScriptedTracker yields deterministic tracks and resets properly."""
    det1 = TrackedDetection(
        local_track_id=1,
        bbox=BoundingBox2D(u_min=10.0, v_min=20.0, u_max=50.0, v_max=80.0),
        confidence=0.9,
    )
    det2 = TrackedDetection(
        local_track_id=2,
        bbox=BoundingBox2D(u_min=15.0, v_min=25.0, u_max=55.0, v_max=85.0),
        confidence=0.85,
    )

    script = {0: [det1], 1: [det1, det2]}
    tracker = ScriptedTracker(script)

    assert tracker.name == "scripted"
    assert isinstance(tracker, PersonTracker)

    # Frame 0
    res0 = tracker.update(None)
    assert len(res0) == 1
    assert res0[0].local_track_id == 1

    # Frame 1
    res1 = tracker.update(None)
    assert len(res1) == 2
    assert res1[1].local_track_id == 2

    # Frame 2 (empty in script)
    res2 = tracker.update(None)
    assert res2 == []

    # Reset
    tracker.reset()
    res_after_reset = tracker.update(None)
    assert len(res_after_reset) == 1
    assert res_after_reset[0].local_track_id == 1


def test_tracking_init_does_not_import_ultralytics_tracker():
    """Verify that importing src.vision.tracking does not import ultralytics_tracker or ultralytics."""
    import src.vision.tracking as pkg
    assert hasattr(pkg, "TrackedDetection")
    assert hasattr(pkg, "PersonTracker")
    assert hasattr(pkg, "ScriptedTracker")
    assert not hasattr(pkg, "UltralyticsPersonTracker")


def test_ultralytics_tracker_mocked():
    """Verify UltralyticsPersonTracker without real weights or network access."""
    mock_yolo_cls = MagicMock()
    mock_instance = MagicMock()
    mock_yolo_cls.return_value = mock_instance

    # Mock detection result with boxes and track IDs
    mock_result = MagicMock()
    mock_boxes = MagicMock()

    # Create mock tensor objects with .cpu().tolist() and .int().cpu().tolist()
    xyxy_mock = MagicMock()
    xyxy_mock.cpu().tolist.return_value = [[10.0, 20.0, 50.0, 90.0], [100.0, 110.0, 150.0, 180.0]]
    mock_boxes.xyxy = xyxy_mock

    conf_mock = MagicMock()
    conf_mock.cpu().tolist.return_value = [0.88, 0.75]
    mock_boxes.conf = conf_mock

    id_mock = MagicMock()
    id_mock.int().cpu().tolist.return_value = [101, 102]
    mock_boxes.id = id_mock

    mock_result.boxes = mock_boxes
    mock_instance.track.return_value = [mock_result]

    with patch.dict(sys.modules, {"ultralytics": MagicMock(YOLO=mock_yolo_cls)}):
        tracker = UltralyticsPersonTracker(
            model="yolo11n.pt",
            device="cpu",
            tracker_cfg="bytetrack.yaml",
            min_confidence=0.35,
            models_dir="data/models",
        )

        assert "ultralytics" in tracker.name
        detections = tracker.update("fake_frame")

        assert len(detections) == 2
        assert detections[0].local_track_id == 101
        assert detections[0].confidence == 0.88
        assert detections[0].bbox.u_min == 10.0
        assert detections[0].bbox.v_max == 90.0
        assert detections[1].local_track_id == 102

        # Verify track arguments
        mock_instance.track.assert_called_once_with(
            source="fake_frame",
            persist=True,
            tracker="bytetrack.yaml",
            classes=[0],
            conf=0.35,
            device="cpu",
            verbose=False,
        )

        # Test when boxes.id is None (untracked frame)
        mock_boxes.id = None
        untracked = tracker.update("fake_frame_2")
        assert untracked == []

        # Test when boxes is None
        mock_result.boxes = None
        empty_det = tracker.update("fake_frame_3")
        assert empty_det == []

        # Test reset creates fresh YOLO instance (Option B)
        tracker.reset()
        assert mock_yolo_cls.call_count == 2


def test_ultralytics_import_error():
    """Verify that a descriptive RuntimeError is raised if ultralytics is not available."""
    with patch.dict(sys.modules, {"ultralytics": None}):
        with pytest.raises(RuntimeError, match="Vision extra not installed"):
            UltralyticsPersonTracker(
                model="yolo11n.pt",
                device="cpu",
                tracker_cfg="bytetrack.yaml",
                min_confidence=0.35,
                models_dir="data/models",
            )
