"""Regression tests for the independent adversarial review of the M3/M3b code (2026-10-13).

Each test reproduces a bug that existed and passed the previous suite; the number refers to the
review finding. They run against the real app (tmp DB via the autouse conftest fixture).
"""
import json

import pytest
from fastapi.testclient import TestClient

from src.access.config import AccessConfig, Door
from src.main import app
from src.rules.policy import load_access_policy
from src.twin.bootstrap import TwinBootstrapService
from src.vision.geometry import BoundingBox2D, CameraObservation

BOX = BoundingBox2D(800.0, 400.0, 900.0, 700.0)


def _obs(track_id, ts, camera="cam_workspace_01", person_id=None):
    return CameraObservation(camera_id=camera, track_id=track_id, bbox=BOX, timestamp=ts, person_id=person_id)


# 1 -------------------------------------------------------------------------------------------
def test_01_mixed_naive_and_aware_timestamps_on_one_track_do_not_crash():
    with TestClient(app) as client:
        r1 = client.post("/api/vision/observations", json={
            "camera_id": "cam_workspace_01", "track_id": "trk_mix",
            "bottom_center": {"u": 960, "v": 700}, "timestamp": "2026-01-15T10:00:00"})
        r2 = client.post("/api/vision/observations", json={
            "camera_id": "cam_workspace_01", "track_id": "trk_mix",
            "bottom_center": {"u": 960, "v": 700}, "timestamp": "2026-01-15T10:00:05Z"})
        r3 = client.post("/api/vision/observations", json={
            "camera_id": "cam_workspace_01", "track_id": "trk_mix",
            "bottom_center": {"u": 960, "v": 700}, "timestamp": "2026-01-15T05:00:10-05:00"})
        assert (r1.status_code, r2.status_code, r3.status_code) == (200, 200, 200)

        events, _ = app.state.twin_engine.store.get_events(limit=50, track_id="trk_mix")
        # every stored timestamp is in the single canonical fixed-width format
        assert {e.timestamp for e in events} == {
            "2026-01-15T10:00:00.000000Z", "2026-01-15T10:00:05.000000Z", "2026-01-15T10:00:10.000000Z"}


# 2 + 5 -----------------------------------------------------------------------------------------
def _after_hours_incident(client, track):
    app.state.observation_pipeline.process_observation(_obs(track, "2026-01-15T21:00:00Z"))
    incs = [i for i in app.state.twin_engine.store.get_incidents(limit=50) if i.track_id == track]
    assert len(incs) == 1 and incs[0].incident_type == "AFTER_HOURS_BREACH"
    return incs[0].incident_id


def test_02_investigating_status_survives_new_evidence():
    with TestClient(app) as client:
        inc_id = _after_hours_incident(client, "trk_inv")
        assert client.patch(f"/api/twin/incidents/{inc_id}", json={"status": "INVESTIGATING"}).status_code == 200
        app.state.observation_pipeline.process_observation(_obs("trk_inv", "2026-01-15T21:00:30Z"))
        got = client.get(f"/api/twin/incidents/{inc_id}").json()
        assert got["status"] == "INVESTIGATING"          # was silently reverted to OPEN
        assert len(got["supporting_event_ids"]) == 2     # and the new evidence was still attached


def test_05_reopen_clears_end_time_and_keeps_dedupe():
    with TestClient(app) as client:
        inc_id = _after_hours_incident(client, "trk_reopen")
        client.patch(f"/api/twin/incidents/{inc_id}", json={"status": "RESOLVED"})
        reopened = client.patch(f"/api/twin/incidents/{inc_id}", json={"status": "OPEN"}).json()
        assert reopened["status"] == "OPEN" and reopened["end_time"] is None
        app.state.observation_pipeline.process_observation(_obs("trk_reopen", "2026-01-15T21:01:00Z"))
        open_for_track = [i for i in app.state.twin_engine.store.get_incidents(status="OPEN", limit=50)
                          if i.track_id == "trk_reopen"]
        assert [i.incident_id for i in open_for_track] == [inc_id]   # no duplicate incident


# 6 -------------------------------------------------------------------------------------------
def test_06_restart_does_not_resurrect_lost_tracks(tmp_path):
    db = f"sqlite:///{tmp_path / 'restart.db'}"
    eng = TwinBootstrapService.bootstrap(db_path=db, seed_path="src/twin/floorplan_seed.yaml")
    eng.ingest_observation("trk_l", "cam_workspace_01", 20.0, 5.0, "2026-01-15T10:00:00Z")
    assert len(eng.prune_tracks(now_iso="2026-01-15T10:05:00Z")) == 1
    assert eng.state.get_active_tracks() == []

    restarted = TwinBootstrapService.bootstrap(db_path=db, seed_path="src/twin/floorplan_seed.yaml")
    assert restarted.state.get_active_tracks() == []
    assert restarted.prune_tracks(now_iso="2026-01-15T10:10:00Z") == []   # no duplicate TRACK_LOST


# 7 -------------------------------------------------------------------------------------------
def test_07_known_person_is_not_reported_unauthorized_when_id_is_omitted_later():
    with TestClient(app):
        pipe = app.state.observation_pipeline
        pipe.process_observation(_obs("trk_admin", "2026-01-15T10:00:00Z", person_id="person_it_admin_01"))
        # same track walks into the server room; this observation does not repeat the person_id
        app.state.access_service.ingest("door_server_01", "badge_0001", "GRANTED", "2026-01-15T10:00:05Z")
        pipe.process_observation(_obs("trk_admin", "2026-01-15T10:00:10Z", camera="cam_server_01"))
        types = [i.incident_type for i in app.state.twin_engine.store.get_incidents(limit=50)
                 if i.track_id == "trk_admin"]
        assert "UNAUTHORIZED_ENTRY" not in types


# 8 -------------------------------------------------------------------------------------------
def test_08_zone_with_two_badge_doors_accepts_a_badge_at_either_door():
    with TestClient(app):
        corr = app.state.rule_engine.correlation
        cfg = corr.access_config
        corr.access_config = AccessConfig(
            doors={**cfg.doors, "door_server_02": Door(
                door_id="door_server_02", name="Server Room Back Door", zone_id="zone_server_room",
                camera_id="cam_server_01", badge_required=True)},
            badges=cfg.badges,
        )
        app.state.access_service.ingest("door_server_01", "badge_0001", "GRANTED", "2026-01-15T10:00:00Z")
        app.state.observation_pipeline.process_observation(
            _obs("trk_two_doors", "2026-01-15T10:00:03Z", camera="cam_server_01", person_id="person_it_admin_01"))
        types = [i.incident_type for i in app.state.twin_engine.store.get_incidents(limit=50)
                 if i.track_id == "trk_two_doors"]
        assert "ACCESS_WITHOUT_BADGE" not in types


# 9 -------------------------------------------------------------------------------------------
def test_09_dedupe_reload_is_not_capped(tmp_path):
    from src.rules.engine import IncidentRuleEngine
    from src.twin.models import Incident

    eng = TwinBootstrapService.bootstrap(db_path=f"sqlite:///{tmp_path / 'many.db'}",
                                         seed_path="src/twin/floorplan_seed.yaml")
    for i in range(520):
        eng.store.insert_incident(Incident(
            incident_id=f"inc_{i:04d}", incident_type="AFTER_HOURS_BREACH", severity="LOW",
            track_id=f"trk_{i:04d}", start_time=f"2026-01-15T20:{i // 60:02d}:{i % 60:02d}Z", summary="t"))
    rule_engine = IncidentRuleEngine(eng.store, eng.state, load_access_policy("data/rules/access_policy.yaml"))
    assert ("AFTER_HOURS_BREACH", "trk_0000") in rule_engine._open_incidents   # oldest one too


# 10 ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("zones_yaml", [
    "restricted_zones:\n  zone_server_room:\n    authorized_person_ids: person_it_admin_01\n",
    "restricted_zones:\n  zone_server_room: just-a-string\n",
])
def test_10_malformed_policy_fails_loudly(tmp_path, zones_yaml):
    path = tmp_path / "policy.yaml"
    path.write_text(zones_yaml, encoding="utf-8")
    with pytest.raises(ValueError):
        load_access_policy(str(path))


def test_10_zone_with_empty_body_is_restricted_to_nobody(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("restricted_zones:\n  zone_server_room:\n", encoding="utf-8")
    policy = load_access_policy(str(path))
    assert policy.is_authorized("zone_server_room", None) is False
    assert policy.is_authorized("zone_server_room", "person_it_admin_01") is False


# 12 + 15 -------------------------------------------------------------------------------------
def test_12_malformed_time_filters_are_422_and_15_total_counts_all_matches():
    with TestClient(app) as client:
        assert client.get("/api/twin/events?start_time=garbage").status_code == 422
        assert client.get("/api/twin/tracks/x/path?start_time=garbage").status_code == 422
        assert client.get("/api/twin/heatmap?start_time=garbage").status_code == 422
        for n in range(3):
            app.state.observation_pipeline.process_observation(_obs(f"trk_cnt{n}", "2026-01-15T22:00:00Z"))
        body = client.get("/api/twin/incidents?status=OPEN&limit=1").json()
        assert len(body["incidents"]) == 1
        assert body["total"] == 3


# 13 + 14 -------------------------------------------------------------------------------------
def test_13_scenario_and_clip_names_cannot_escape_their_folders():
    with TestClient(app) as client:
        assert client.post("/api/simulator/scenarios/..%5Crules%5Caccess_policy/run").status_code == 404
        assert client.post("/api/vision/clips/..%5Cscenarios%5Ctailgating/run").status_code == 404


def test_14_invalid_scenario_file_is_logged_not_hidden(tmp_path, caplog):
    from src.simulator.scenarios import list_scenarios

    (tmp_path / "good.yaml").write_text(
        "name: good\nbase_time_utc: '2026-01-15T10:00:00Z'\nsteps:\n"
        "  - {t: 0, camera_id: cam_workspace_01, track_id: a, floor: [20.0, 5.0]}\n", encoding="utf-8")
    (tmp_path / "bad.yaml").write_text("name: bad\nsteps: not-a-list\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        names = [s.name for s in list_scenarios(str(tmp_path))]
    assert names == ["good"]
    assert "bad.yaml" in caplog.text


# 16 ------------------------------------------------------------------------------------------
def test_16_dwell_exceeded_is_emitted_once_per_dwell_period(tmp_path):
    eng = TwinBootstrapService.bootstrap(db_path=f"sqlite:///{tmp_path / 'dwell.db'}",
                                         seed_path="src/twin/floorplan_seed.yaml")
    for sec in range(0, 421, 30):   # 420 s in the server room, observed every 30 s (4 obs past 300 s)
        eng.ingest_observation("trk_dw", "cam_server_01", 35.0, 5.0, f"2026-01-15T10:{sec // 60:02d}:{sec % 60:02d}Z")
    dwell, _ = eng.store.get_events(limit=50, track_id="trk_dw", event_type="DWELL_TIME_EXCEEDED")
    assert len(dwell) == 1
    assert json.loads(dwell[0].payload_json)["dwell_seconds"] > 300
