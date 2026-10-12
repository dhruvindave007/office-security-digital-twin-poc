from dataclasses import FrozenInstanceError
import math
import pytest

from src.vision.geometry import (
    BoundingBox2D,
    CameraObservation,
    FloorPoint,
    PixelPoint,
    ProjectionResult,
    TwinObservation,
)


def test_pixel_point_valid_and_immutable():
    """Verify PixelPoint stores coordinates, prohibits mutation, and serializes."""
    pt = PixelPoint(u=100.5, v=200.25)
    assert pt.u == 100.5
    assert pt.v == 200.25
    assert pt.to_tuple() == (100.5, 200.25)
    assert pt.to_dict() == {"u": 100.5, "v": 200.25}

    with pytest.raises(FrozenInstanceError):
        pt.u = 50.0  # type: ignore


def test_pixel_point_rejection():
    """Verify PixelPoint rejects negative coordinates and non-finite values."""
    with pytest.raises(ValueError, match="cannot be negative"):
        PixelPoint(u=-1.0, v=100.0)

    with pytest.raises(ValueError, match="cannot be negative"):
        PixelPoint(u=100.0, v=-5.0)

    with pytest.raises(ValueError, match="finite"):
        PixelPoint(u=float("nan"), v=50.0)

    with pytest.raises(ValueError, match="finite"):
        PixelPoint(u=50.0, v=float("inf"))


def test_floor_point_distance_and_immutable():
    """Verify FloorPoint euclidean distance calculations and immutability."""
    p1 = FloorPoint(x=0.0, y=0.0)
    p2 = FloorPoint(x=3.0, y=4.0)

    assert math.isclose(p1.distance_to(p2), 5.0)
    assert p1.to_tuple() == (0.0, 0.0)
    assert p2.to_dict() == {"x": 3.0, "y": 4.0}

    with pytest.raises(FrozenInstanceError):
        p1.x = 10.0  # type: ignore


def test_floor_point_rejection():
    """Verify FloorPoint rejects non-finite values."""
    with pytest.raises(ValueError, match="finite"):
        FloorPoint(x=float("nan"), y=1.0)
    with pytest.raises(ValueError, match="finite"):
        FloorPoint(x=1.0, y=float("-inf"))


def test_bounding_box_calculations():
    """Verify BoundingBox2D dimensions, area, and bottom_center contact point."""
    bbox = BoundingBox2D(u_min=100.0, v_min=200.0, u_max=300.0, v_max=500.0)
    assert bbox.width == 200.0
    assert bbox.height == 300.0
    assert bbox.area == 60000.0

    # Ground contact is center horizontally, bottom vertically
    contact = bbox.bottom_center
    assert contact.u == 200.0
    assert contact.v == 500.0


def test_bounding_box_rejections():
    """Verify BoundingBox2D rejects invalid bounds, inverted coordinates, or negatives."""
    with pytest.raises(ValueError, match="u_max"):
        BoundingBox2D(u_min=300.0, v_min=100.0, u_max=200.0, v_max=500.0)

    with pytest.raises(ValueError, match="v_max"):
        BoundingBox2D(u_min=100.0, v_min=500.0, u_max=300.0, v_max=200.0)

    with pytest.raises(ValueError, match="cannot be negative"):
        BoundingBox2D(u_min=-10.0, v_min=0.0, u_max=100.0, v_max=100.0)


def test_camera_observation_derivation_and_rejection():
    """Verify CameraObservation derives bottom_center from bbox and rejects missing coordinates."""
    bbox = BoundingBox2D(u_min=50.0, v_min=100.0, u_max=150.0, v_max=300.0)
    obs = CameraObservation(
        camera_id="cam_01",
        track_id="trk_1",
        bbox=bbox,
    )
    assert obs.bottom_center is not None
    assert obs.bottom_center.u == 100.0
    assert obs.bottom_center.v == 300.0

    # Explicit bottom_center
    obs2 = CameraObservation(
        camera_id="cam_01",
        track_id="trk_2",
        bottom_center=PixelPoint(u=500.0, v=600.0),
    )
    assert obs2.bottom_center.u == 500.0

    # Missing both
    with pytest.raises(ValueError, match="requires either bottom_center or bbox"):
        CameraObservation(camera_id="cam_01", track_id="trk_3")

    # Missing camera_id or track_id
    with pytest.raises(ValueError, match="camera_id"):
        CameraObservation(camera_id="  ", track_id="trk_3", bbox=bbox)
    with pytest.raises(ValueError, match="track_id"):
        CameraObservation(camera_id="cam_01", track_id="", bbox=bbox)


def test_twin_observation_contract():
    """Verify TwinObservation immutable contract."""
    obs = TwinObservation(
        track_id="trk_1",
        camera_id="cam_reception_01",
        floor_point=FloorPoint(x=5.0, y=6.0),
        pixel_point=PixelPoint(u=500.0, v=600.0),
        zone_id="zone_reception",
        emitted_events=[],
        confidence=0.95,
        timestamp="2026-09-19T12:00:00Z",
    )
    assert obs.track_id == "trk_1"
    assert obs.floor_point.x == 5.0
    with pytest.raises(FrozenInstanceError):
        obs.track_id = "trk_2"  # type: ignore
