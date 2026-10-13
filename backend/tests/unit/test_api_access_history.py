import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.twin.models import Camera, TwinEvent, Zone


def _seed_test_data(client: TestClient):
    """Seed twin store with sample zones, cameras, and events."""
    engine = app.state.twin_engine
    store = engine.store

    # 1. Seed zone and camera
    store.insert_zone(
        Zone(
            zone_id="zone_workspace",
            name="Workspace",
            zone_type="workspace",
            security_level=2,
            min_x=0.0,
            min_y=0.0,
            max_x=30.0,
            max_y=20.0,
        )
    )
    store.insert_camera(
        Camera(
            camera_id="cam_workspace_01",
            name="Workspace Cam",
            primary_zone_id="zone_workspace",
        )
    )

    # 2. Append events for track "trk_history_01"
    evt1 = TwinEvent(
        sequence_num=1,
        event_id="01a13000-0000-7000-8000-000000000001",
        event_type="PERSON_DETECTED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_history_01",
        person_id="person_staff_01",
        camera_id="cam_workspace_01",
        zone_id="zone_workspace",
        coordinates_json=json.dumps({"x": 10.0, "y": 8.0}),
        created_at="2026-01-15T10:00:00Z",
    )
    evt2 = TwinEvent(
        sequence_num=2,
        event_id="01a13000-0000-7000-8000-000000000002",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_history_01",
        person_id="person_staff_01",
        camera_id="cam_workspace_01",
        zone_id="zone_workspace",
        coordinates_json=None,
        created_at="2026-01-15T10:00:00Z",
    )
    evt3 = TwinEvent(
        sequence_num=3,
        event_id="01a13000-0000-7000-8000-000000000003",
        event_type="PERSON_DETECTED",
        timestamp="2026-01-15T10:01:00Z",
        track_id="trk_history_01",
        person_id="person_staff_01",
        camera_id="cam_workspace_01",
        zone_id="zone_workspace",
        coordinates_json=json.dumps({"x": 15.0, "y": 12.0}),
        created_at="2026-01-15T10:01:00Z",
    )
    store.append_event(evt1)
    store.append_event(evt2)
    store.append_event(evt3)


def test_api_access_doors():
    """Verify GET /api/access/doors returns configured door definitions."""
    with TestClient(app) as client:
        res = client.get("/api/access/doors")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 2
        door_ids = {d["door_id"] for d in data}
        assert "door_server_01" in door_ids
        assert "door_main_entrance" in door_ids


def test_api_access_events_happy_path():
    """Verify POST /api/access/events ingests a valid swipe event."""
    with TestClient(app) as client:
        payload = {
            "door_id": "door_server_01",
            "badge_id": "badge_0001",
            "result": "GRANTED",
            "timestamp": "2026-01-15T10:00:00Z",
        }
        res = client.post("/api/access/events", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "event_id" in data
        assert data["sequence_num"] >= 1
        assert data["person_id"] == "person_it_admin_01"
        assert isinstance(data["raised_incident_ids"], list)


def test_api_access_events_404_unknown_door():
    """Verify POST /api/access/events returns 404 for unknown door_id."""
    with TestClient(app) as client:
        payload = {
            "door_id": "door_nonexistent_xyz",
            "badge_id": "badge_0001",
            "result": "GRANTED",
            "timestamp": "2026-01-15T10:00:00Z",
        }
        res = client.post("/api/access/events", json=payload)
        assert res.status_code == 404
        assert "unknown" in res.json()["detail"].lower()


def test_api_access_events_422_bad_result():
    """Verify POST /api/access/events returns 422 for invalid result value."""
    with TestClient(app) as client:
        payload = {
            "door_id": "door_server_01",
            "badge_id": "badge_0001",
            "result": "INVALID_OUTCOME",
            "timestamp": "2026-01-15T10:00:00Z",
        }
        res = client.post("/api/access/events", json=payload)
        assert res.status_code == 422
        assert "granted" in res.json()["detail"].lower() or "denied" in res.json()["detail"].lower()


def test_api_access_service_503(monkeypatch):
    """Verify 503 is returned if access_service is uninitialized."""
    with TestClient(app) as client:
        orig = getattr(app.state, "access_service", None)
        try:
            app.state.access_service = None
            res_doors = client.get("/api/access/doors")
            assert res_doors.status_code == 503
            assert "not initialized" in res_doors.json()["detail"].lower()

            res_evt = client.post(
                "/api/access/events",
                json={
                    "door_id": "door_server_01",
                    "badge_id": "badge_0001",
                    "result": "GRANTED",
                    "timestamp": "2026-01-15T10:00:00Z",
                },
            )
            assert res_evt.status_code == 503
        finally:
            app.state.access_service = orig


def test_api_twin_track_path_200_and_404():
    """Verify GET /api/twin/tracks/{track_id}/path returns ordered path points or 404."""
    with TestClient(app) as client:
        _seed_test_data(client)

        # 1. 200 for track with points
        res_ok = client.get("/api/twin/tracks/trk_history_01/path")
        assert res_ok.status_code == 200
        points = res_ok.json()
        assert len(points) == 2
        assert points[0]["x"] == 10.0
        assert points[0]["y"] == 8.0
        assert points[1]["x"] == 15.0
        assert points[1]["y"] == 12.0

        # 2. 404 for track with no points
        res_404 = client.get("/api/twin/tracks/trk_no_points_xyz/path")
        assert res_404.status_code == 404
        assert "no path points" in res_404.json()["detail"].lower()


def test_api_twin_occupancy_history_200_and_422():
    """Verify GET /api/twin/occupancy/history returns time series and enforces all 422 rules."""
    with TestClient(app) as client:
        _seed_test_data(client)

        # 1. 200 happy path
        res_ok = client.get(
            "/api/twin/occupancy/history?start_time=2026-01-15T09:59:00Z&end_time=2026-01-15T10:05:00Z&bucket_seconds=60"
        )
        assert res_ok.status_code == 200
        buckets = res_ok.json()
        assert len(buckets) == 6
        assert "zone_workspace" in buckets[0]["occupancy"]

        # 2. 200 with zone_id filter
        res_zone = client.get(
            "/api/twin/occupancy/history?start_time=2026-01-15T09:59:00Z&end_time=2026-01-15T10:05:00Z&bucket_seconds=60&zone_id=zone_workspace"
        )
        assert res_zone.status_code == 200
        zone_buckets = res_zone.json()
        assert len(zone_buckets) == 6
        assert list(zone_buckets[0]["occupancy"].keys()) == ["zone_workspace"]

        # 3. 422 when missing start_time or end_time
        res_missing = client.get("/api/twin/occupancy/history?start_time=2026-01-15T09:59:00Z")
        assert res_missing.status_code == 422

        # 4. 422 when end_time <= start_time
        res_inverted = client.get(
            "/api/twin/occupancy/history?start_time=2026-01-15T10:05:00Z&end_time=2026-01-15T10:00:00Z"
        )
        assert res_inverted.status_code == 422
        assert "greater than" in res_inverted.json()["detail"].lower()

        # 5. 422 when bucket_seconds < 1
        res_bad_bucket = client.get(
            "/api/twin/occupancy/history?start_time=2026-01-15T10:00:00Z&end_time=2026-01-15T10:05:00Z&bucket_seconds=0"
        )
        assert res_bad_bucket.status_code == 422
        assert "at least 1" in res_bad_bucket.json()["detail"].lower()

        # 6. 422 when bucket count exceeds 1440
        # 2 days (172800 seconds) with 60s buckets = 2880 buckets > 1440
        res_too_many = client.get(
            "/api/twin/occupancy/history?start_time=2026-01-15T00:00:00Z&end_time=2026-01-17T00:00:00Z&bucket_seconds=60"
        )
        assert res_too_many.status_code == 422
        assert "1440" in res_too_many.json()["detail"]


def test_api_twin_heatmap_200_and_422():
    """Verify GET /api/twin/heatmap returns spatial grid and enforces bounds."""
    with TestClient(app) as client:
        _seed_test_data(client)

        # 1. 200 happy path
        res_ok = client.get(
            "/api/twin/heatmap?start_time=2026-01-15T00:00:00Z&end_time=2026-01-16T00:00:00Z&cell_size_m=2.0"
        )
        assert res_ok.status_code == 200
        grid = res_ok.json()
        assert grid["cell_size_m"] == 2.0
        assert "cells" in grid
        assert grid["cols"] > 0
        assert grid["rows"] > 0

        # 2. 422 when cell_size_m < 0.25
        res_small = client.get("/api/twin/heatmap?cell_size_m=0.1")
        assert res_small.status_code == 422
        assert "0.25" in res_small.json()["detail"]

        # 3. 422 when cell_size_m > 10.0
        res_large = client.get("/api/twin/heatmap?cell_size_m=15.0")
        assert res_large.status_code == 422
        assert "10.0" in res_large.json()["detail"]

        # 4. 422 when end_time <= start_time
        res_bad_time = client.get(
            "/api/twin/heatmap?start_time=2026-01-16T00:00:00Z&end_time=2026-01-15T00:00:00Z"
        )
        assert res_bad_time.status_code == 422
        assert "greater than" in res_bad_time.json()["detail"].lower()
