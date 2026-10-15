import pytest
from src.twin.validators import EventValidator, PolygonValidator


def test_polygon_validator_valid():
    """Verify PolygonValidator accepts valid convex and non-convex polygons."""
    # Rectangle
    rect_coords = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    res = PolygonValidator.validate_vertices(rect_coords)
    assert res.min_x == 0.0
    assert res.min_y == 0.0
    assert res.max_x == 10.0
    assert res.max_y == 5.0
    assert res.area == 50.0

    # Auto-closed polygon (start == end)
    closed_coords = [(0.0, 0.0), (5.0, 0.0), (0.0, 5.0), (0.0, 0.0)]
    res2 = PolygonValidator.validate_vertices(closed_coords)
    assert res2.area == 12.5


def test_polygon_validator_invalid():
    """Verify PolygonValidator rejects degenerate or invalid polygons."""
    # Fewer than 3 points
    with pytest.raises(ValueError, match="at least 3 vertices"):
        PolygonValidator.validate_vertices([(0.0, 0.0), (1.0, 1.0)])

    # Self-intersecting bow-tie polygon
    bowtie = [(0.0, 0.0), (10.0, 10.0), (0.0, 10.0), (10.0, 0.0)]
    with pytest.raises(ValueError, match="invalid"):
        PolygonValidator.validate_vertices(bowtie)

    # Collinear points (zero area / degenerate geometry)
    collinear = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
    with pytest.raises(ValueError, match=r"(strictly positive|invalid)"):
        PolygonValidator.validate_vertices(collinear)


def test_event_validator():
    """Verify EventValidator checks allowed types and attributes."""
    assert EventValidator.validate_event_type("PERSON_DETECTED") == "PERSON_DETECTED"
    assert EventValidator.validate_event_type("CROSS_ZONE_TRANSITION") == "CROSS_ZONE_TRANSITION"

    with pytest.raises(ValueError, match="Invalid event_type"):
        EventValidator.validate_event_type("NON_EXISTENT_EVENT")

    assert EventValidator.validate_track_id("trk_101") == "trk_101"
    with pytest.raises(ValueError, match="cannot be empty"):
        EventValidator.validate_track_id("   ")

    assert EventValidator.validate_timestamp("2026-09-19T12:00:00Z") == "2026-09-19T12:00:00.000000Z"
    # Normalisation: every accepted format maps to ONE canonical fixed-width UTC string
    assert EventValidator.validate_timestamp("2026-09-19T12:00:00+00:00") == "2026-09-19T12:00:00.000000Z"
    assert EventValidator.validate_timestamp("2026-09-19T12:00:00") == "2026-09-19T12:00:00.000000Z"
    assert EventValidator.validate_timestamp("2026-09-19T07:00:00-05:00") == "2026-09-19T12:00:00.000000Z"
    with pytest.raises(ValueError, match="Invalid ISO-8601"):
        EventValidator.validate_timestamp("not-a-timestamp")

    assert EventValidator.validate_coordinates((12.5, 8.4)) == (12.5, 8.4)
    with pytest.raises(ValueError, match="finite"):
        EventValidator.validate_coordinates((float("nan"), 0.0))

    assert EventValidator.validate_confidence(0.85) == 0.85
    with pytest.raises(ValueError, match="range"):
        EventValidator.validate_confidence(1.5)
