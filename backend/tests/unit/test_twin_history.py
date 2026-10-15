import json
import math
from datetime import datetime, timezone
import pytest

from src.twin.history import occupancy_series, position_heatmap, track_path
from src.twin.models import TwinEvent


def _make_event(
    seq: int,
    event_type: str,
    timestamp: str,
    track_id: str = "trk_01",
    zone_id: str = "zone_workspace",
    camera_id: str = "cam_01",
    coordinates: tuple[float, float] | None = None,
) -> TwinEvent:
    coords_json = json.dumps({"x": coordinates[0], "y": coordinates[1]}) if coordinates else None
    return TwinEvent(
        sequence_num=seq,
        event_id=f"evt_{seq:04d}",
        event_type=event_type,
        timestamp=timestamp,
        track_id=track_id,
        person_id="person_01",
        camera_id=camera_id,
        zone_id=zone_id,
        previous_zone_id=None,
        confidence=0.95,
        coordinates_json=coords_json,
        payload_json="{}",
        created_at=timestamp,
    )


def test_track_path_ordering_and_coordinate_skipping():
    """Verify track_path orders points ascending by sequence_num and skips missing/invalid coordinates."""
    # Events passed in shuffled/descending sequence_num order
    evts = [
        _make_event(3, "PERSON_DETECTED", "2026-01-15T10:00:10Z", coordinates=(25.0, 8.0)),
        _make_event(1, "PERSON_DETECTED", "2026-01-15T10:00:00Z", coordinates=(20.0, 5.0)),
        _make_event(2, "PERSON_DETECTED", "2026-01-15T10:00:05Z", coordinates=None),  # No coords -> skipped
        _make_event(4, "ZONE_ENTERED", "2026-01-15T10:00:12Z", coordinates=(26.0, 8.5)),  # Non-PERSON_DETECTED -> skipped
    ]

    # Test coordinate parsing edge case: list coordinates format [x, y]
    evt_list_coords = _make_event(5, "PERSON_DETECTED", "2026-01-15T10:00:15Z")
    evt_list_coords.coordinates_json = json.dumps([28.0, 9.0])
    evts.append(evt_list_coords)

    # Test invalid JSON string -> skipped
    evt_bad_json = _make_event(6, "PERSON_DETECTED", "2026-01-15T10:00:20Z")
    evt_bad_json.coordinates_json = "{bad-json"
    evts.append(evt_bad_json)

    points = track_path(evts)
    assert len(points) == 3

    # Strictly ascending by sequence_num
    assert points[0]["event_id"] == "evt_0001"
    assert points[0]["x"] == 20.0
    assert points[0]["y"] == 5.0
    assert points[0]["camera_id"] == "cam_01"
    assert points[0]["zone_id"] == "zone_workspace"

    assert points[1]["event_id"] == "evt_0003"
    assert points[1]["x"] == 25.0
    assert points[1]["y"] == 8.0

    assert points[2]["event_id"] == "evt_0005"
    assert points[2]["x"] == 28.0
    assert points[2]["y"] == 9.0


def test_occupancy_series_exact_boundaries_and_transitions():
    """Verify occupancy_series samples state at exact bucket boundaries from start to end."""
    events = [
        # t=09:59:30 (before start): trk_01 enters zone_reception
        _make_event(1, "ZONE_ENTERED", "2026-01-15T09:59:30Z", track_id="trk_01", zone_id="zone_reception"),
        # t=10:00:30 (during bucket 1): trk_01 transitions reception -> workspace
        _make_event(2, "CROSS_ZONE_TRANSITION", "2026-01-15T10:00:30Z", track_id="trk_01", zone_id="zone_workspace"),
        # t=10:00:45: trk_02 enters zone_server_room
        _make_event(3, "ZONE_ENTERED", "2026-01-15T10:00:45Z", track_id="trk_02", zone_id="zone_server_room"),
        # t=10:01:30 (during bucket 2): trk_02 exits server room
        _make_event(4, "ZONE_EXITED", "2026-01-15T10:01:30Z", track_id="trk_02", zone_id="zone_server_room"),
        # t=10:02:15: trk_01 lost
        _make_event(5, "TRACK_LOST", "2026-01-15T10:02:15Z", track_id="trk_01", zone_id="zone_workspace"),
    ]

    start = "2026-01-15T10:00:00Z"
    end = "2026-01-15T10:03:00Z"
    bucket_sec = 60

    series = occupancy_series(events, start=start, end=end, bucket_seconds=bucket_sec)

    # Buckets: 10:00:00, 10:01:00, 10:02:00 (exclusive of 10:03:00)
    assert len(series) == 3

    # Bucket 0 at 10:00:00 -> state from events <= 10:00:00 (trk_01 in zone_reception)
    b0 = series[0]
    assert b0["bucket_start"] == "2026-01-15T10:00:00+00:00"
    assert b0["occupancy"]["zone_reception"] == 1
    assert b0["occupancy"]["zone_workspace"] == 0
    assert b0["occupancy"]["zone_server_room"] == 0

    # Bucket 1 at 10:01:00 -> state after event 2 & 3 applied (trk_01 in workspace, trk_02 in server room)
    b1 = series[1]
    assert b1["bucket_start"] == "2026-01-15T10:01:00+00:00"
    assert b1["occupancy"]["zone_reception"] == 0
    assert b1["occupancy"]["zone_workspace"] == 1
    assert b1["occupancy"]["zone_server_room"] == 1

    # Bucket 2 at 10:02:00 -> state after event 4 applied (trk_02 exited, trk_01 still in workspace)
    b2 = series[2]
    assert b2["bucket_start"] == "2026-01-15T10:02:00+00:00"
    assert b2["occupancy"]["zone_reception"] == 0
    assert b2["occupancy"]["zone_workspace"] == 1
    assert b2["occupancy"]["zone_server_room"] == 0


def test_occupancy_series_ignores_badge_tracks():
    """Verify occupancy_series ignores tracks starting with badge:*."""
    events = [
        _make_event(1, "ZONE_ENTERED", "2026-01-15T10:00:00Z", track_id="badge:badge_0001", zone_id="zone_server_room"),
        _make_event(2, "ZONE_ENTERED", "2026-01-15T10:00:10Z", track_id="sim_trk_real", zone_id="zone_workspace"),
    ]

    series = occupancy_series(events, start="2026-01-15T10:00:00Z", end="2026-01-15T10:01:00Z", bucket_seconds=60)
    assert len(series) == 1
    occ = series[0]["occupancy"]
    # Only zone_workspace should be tracked from real tracks
    assert "zone_server_room" not in occ
    assert occ.get("zone_workspace") == 0  # Event at 10:00:10 is after boundary 10:00:00


def test_position_heatmap_placement_and_clamping():
    """Verify position_heatmap spatial binning and edge-clamping."""
    width_m = 30.0
    height_m = 15.0
    cell_size = 5.0

    # cols = 30 / 5 = 6, rows = 15 / 5 = 3
    evts = [
        # Inside cell (col=0, row=0): [2.0, 3.0]
        _make_event(1, "PERSON_DETECTED", "2026-01-15T10:00:00Z", coordinates=(2.0, 3.0)),
        _make_event(2, "PERSON_DETECTED", "2026-01-15T10:00:01Z", coordinates=(4.5, 1.0)),
        # Inside cell (col=2, row=1): [12.0, 7.0]
        _make_event(3, "PERSON_DETECTED", "2026-01-15T10:00:02Z", coordinates=(12.0, 7.0)),
        # Out-of-bounds negative -> clamped to (0, 0)
        _make_event(4, "PERSON_DETECTED", "2026-01-15T10:00:03Z", coordinates=(-5.0, -2.0)),
        # Out-of-bounds positive -> clamped to (cols-1, rows-1) = (5, 2)
        _make_event(5, "PERSON_DETECTED", "2026-01-15T10:00:04Z", coordinates=(40.0, 20.0)),
        # Non-PERSON_DETECTED -> ignored
        _make_event(6, "ZONE_ENTERED", "2026-01-15T10:00:05Z", coordinates=(2.0, 3.0)),
    ]

    heatmap = position_heatmap(evts, width_m=width_m, height_m=height_m, cell_size_m=cell_size)

    assert heatmap["width_m"] == 30.0
    assert heatmap["height_m"] == 15.0
    assert heatmap["cell_size_m"] == 5.0
    assert heatmap["cols"] == 6
    assert heatmap["rows"] == 3

    cells = heatmap["cells"]
    assert len(cells) == 3
    assert len(cells[0]) == 6

    # Cell (0, 0) has 2 points + 1 clamped negative point = 3
    assert cells[0][0] == 3

    # Cell (1, 2) [row 1, col 2] has 1 point
    assert cells[1][2] == 1

    # Cell (2, 5) [row 2, col 5] has 1 clamped point
    assert cells[2][5] == 1
