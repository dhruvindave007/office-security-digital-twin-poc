import pytest
from fastapi.testclient import TestClient

from src.config import get_settings
from src.main import app


def test_vision_observations_api(tmp_path, monkeypatch):
    """POST /api/vision/observations for cam_reception_01 returns 200 and zone_id, unknown camera returns 404."""
    db_file = tmp_path / "test_api_vision.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    get_settings.cache_clear()

    try:
        with TestClient(app) as client:
            # 1. Valid observation on cam_reception_01
            res = client.post(
                "/api/vision/observations",
                json={
                    "camera_id": "cam_reception_01",
                    "track_id": "trk_rec_001",
                    "bottom_center": {"u": 960.0, "v": 800.0},
                },
            )
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["track_id"] == "trk_rec_001"
            assert data["camera_id"] == "cam_reception_01"
            assert data["zone_id"] == "zone_reception"
            assert "floor_point" in data
            assert isinstance(data["emitted_event_ids"], list)

            # 2. Unknown camera returns 404
            res_unknown = client.post(
                "/api/vision/observations",
                json={
                    "camera_id": "cam_non_existent",
                    "track_id": "trk_rec_002",
                    "bottom_center": {"u": 960.0, "v": 800.0},
                },
            )
            assert res_unknown.status_code == 404
            assert "not found" in res_unknown.json()["detail"].lower()
    finally:
        get_settings.cache_clear()


def test_simulator_api_routes(tmp_path, monkeypatch):
    """Verify GET /api/simulator/scenarios and POST run endpoints."""
    db_file = tmp_path / "test_api_sim.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    get_settings.cache_clear()

    try:
        with TestClient(app) as client:
            # 1. GET /api/simulator/scenarios
            res_list = client.get("/api/simulator/scenarios")
            assert res_list.status_code == 200
            scenarios = res_list.json()
            assert isinstance(scenarios, list)
            assert len(scenarios) == 4
            names = {s["name"] for s in scenarios}
            assert "normal_workday" in names
            assert "server_room_unauthorized_entry" in names
            assert "reception_over_capacity" in names
            assert "after_hours_presence" in names

            # 2. POST /api/simulator/scenarios/{name}/run (happy path)
            res_run = client.post("/api/simulator/scenarios/server_room_unauthorized_entry/run")
            assert res_run.status_code == 200
            run_data = res_run.json()
            assert run_data["scenario_name"] == "server_room_unauthorized_entry"
            assert run_data["steps_processed"] == 14
            assert run_data["events_emitted"] == 20
            assert len(run_data["incidents"]) == 3
            assert run_data["errors"] == []

            # 3. POST /api/simulator/scenarios/{name}/run (unknown scenario -> 404)
            res_bad = client.post("/api/simulator/scenarios/unknown_scenario_xyz/run")
            assert res_bad.status_code == 404
            assert "not found" in res_bad.json()["detail"].lower()
    finally:
        get_settings.cache_clear()


def test_incidents_get_and_patch_api(tmp_path, monkeypatch):
    """Verify GET /api/twin/incidents/{id} and PATCH status update behavior."""
    db_file = tmp_path / "test_api_inc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    get_settings.cache_clear()

    try:
        with TestClient(app) as client:
            # Run scenario to generate incidents
            res_run = client.post("/api/simulator/scenarios/server_room_unauthorized_entry/run")
            assert res_run.status_code == 200
            run_data = res_run.json()
            assert len(run_data["incidents"]) >= 1
            inc_id = run_data["incidents"][0]

            # 1. GET incident (200)
            res_get = client.get(f"/api/twin/incidents/{inc_id}")
            assert res_get.status_code == 200
            inc_data = res_get.json()
            assert inc_data["incident_id"] == inc_id
            assert inc_data["status"] == "OPEN"
            assert inc_data["end_time"] is None

            # 2. GET incident (404 for unknown)
            res_get_404 = client.get("/api/twin/incidents/unknown_non_existent_id")
            assert res_get_404.status_code == 404

            # 3. PATCH incident -> INVESTIGATING (200)
            res_patch_inv = client.patch(
                f"/api/twin/incidents/{inc_id}",
                json={"status": "INVESTIGATING"},
            )
            assert res_patch_inv.status_code == 200
            assert res_patch_inv.json()["status"] == "INVESTIGATING"
            assert res_patch_inv.json()["end_time"] is None

            # 4. PATCH incident -> RESOLVED (200, end_time set)
            res_patch_res = client.patch(
                f"/api/twin/incidents/{inc_id}",
                json={"status": "RESOLVED"},
            )
            assert res_patch_res.status_code == 200
            patch_data = res_patch_res.json()
            assert patch_data["status"] == "RESOLVED"
            assert patch_data["end_time"] is not None

            # 5. PATCH invalid status -> 422
            res_patch_bad = client.patch(
                f"/api/twin/incidents/{inc_id}",
                json={"status": "INVALID_CUSTOM_STATUS"},
            )
            assert res_patch_bad.status_code == 422

            # 6. PATCH unknown incident -> 404
            res_patch_404 = client.patch(
                "/api/twin/incidents/unknown_non_existent_id",
                json={"status": "RESOLVED"},
            )
            assert res_patch_404.status_code == 404
    finally:
        get_settings.cache_clear()


def test_twin_state_dwell_seconds_utc_regression(tmp_path, monkeypatch):
    """Regression test for dwell_seconds UTC bug with naive and aware timestamps."""
    from datetime import datetime, timezone
    import src.api.routes_twin as routes_twin

    db_file = tmp_path / "test_api_dwell.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    get_settings.cache_clear()

    fixed_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(routes_twin, "datetime", MockDatetime)

    try:
        with TestClient(app) as client:
            engine = app.state.twin_engine

            # Track 1: naive dwell_start (100 seconds before fixed_now)
            engine.state.update_track(
                track_id="trk_naive_dwell",
                camera_id="cam_reception_01",
                zone_id="zone_reception",
                position=(5.0, 5.0),
                timestamp="2026-01-15T11:58:20",
            )

            # Track 2: aware dwell_start with +00:00 (100 seconds before fixed_now)
            engine.state.update_track(
                track_id="trk_aware_dwell",
                camera_id="cam_reception_01",
                zone_id="zone_reception",
                position=(5.0, 5.0),
                timestamp="2026-01-15T11:58:20+00:00",
            )

            res = client.get("/api/twin/state")
            assert res.status_code == 200
            data = res.json()
            tracks = {t["track_id"]: t for t in data["active_tracks"]}

            assert "trk_naive_dwell" in tracks
            assert "trk_aware_dwell" in tracks

            # Check dwell_seconds is within 2 s of 100.0s for both naive and aware
            dwell_naive = tracks["trk_naive_dwell"]["dwell_seconds"]
            dwell_aware = tracks["trk_aware_dwell"]["dwell_seconds"]

            assert abs(dwell_naive - 100.0) <= 2.0, f"Naive dwell {dwell_naive} not within 2s of 100.0"
            assert abs(dwell_aware - 100.0) <= 2.0, f"Aware dwell {dwell_aware} not within 2s of 100.0"
    finally:
        get_settings.cache_clear()
