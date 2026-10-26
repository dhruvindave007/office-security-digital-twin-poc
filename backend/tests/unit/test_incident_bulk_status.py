"""Operator status changes: bulk endpoint, and every change is pushed to open dashboards."""
from fastapi.testclient import TestClient

from src.config import get_settings
from src.main import app


def _client(tmp_path, monkeypatch, name):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / name}")
    get_settings.cache_clear()
    return TestClient(app)


def _record_pushes(client, monkeypatch):
    pushed = []
    monkeypatch.setattr(client.app.state.broadcaster, "publish", lambda event, data: pushed.append((event, data)))
    return pushed


def test_bulk_status_updates_all_and_reports_unknown(tmp_path, monkeypatch):
    try:
        with _client(tmp_path, monkeypatch, "bulk.db") as client:
            ids = client.post("/api/simulator/scenarios/server_room_unauthorized_entry/run").json()["incidents"]
            assert len(ids) >= 2
            pushed = _record_pushes(client, monkeypatch)

            res = client.post("/api/twin/incidents/status",
                              json={"incident_ids": ids + ["no_such_incident", ids[0]], "status": "FALSE_POSITIVE"})
            assert res.status_code == 200, res.text
            body = res.json()
            assert [i["incident_id"] for i in body["updated"]] == ids          # duplicates applied once, order kept
            assert all(i["status"] == "FALSE_POSITIVE" and i["end_time"] for i in body["updated"])
            assert body["not_found"] == ["no_such_incident"]

            for incident_id in ids:                                               # stored, not just echoed
                assert client.get(f"/api/twin/incidents/{incident_id}").json()["status"] == "FALSE_POSITIVE"
            assert [(e, d["incident_id"]) for e, d in pushed] == [("incident", i) for i in ids]

            reopened = client.post("/api/twin/incidents/status", json={"incident_ids": ids[:1], "status": "OPEN"}).json()
            assert reopened["updated"][0]["status"] == "OPEN" and reopened["updated"][0]["end_time"] is None
    finally:
        get_settings.cache_clear()


def test_bulk_status_validates_input(tmp_path, monkeypatch):
    try:
        with _client(tmp_path, monkeypatch, "bulk_bad.db") as client:
            assert client.post("/api/twin/incidents/status", json={"incident_ids": [], "status": "RESOLVED"}).status_code == 422
            assert client.post("/api/twin/incidents/status", json={"incident_ids": ["x"], "status": "DONE"}).status_code == 422
            too_many = [f"i{n}" for n in range(501)]
            assert client.post("/api/twin/incidents/status", json={"incident_ids": too_many, "status": "RESOLVED"}).status_code == 422
    finally:
        get_settings.cache_clear()


def test_single_patch_is_pushed_to_dashboards(tmp_path, monkeypatch):
    try:
        with _client(tmp_path, monkeypatch, "patch_push.db") as client:
            inc_id = client.post("/api/simulator/scenarios/server_room_unauthorized_entry/run").json()["incidents"][0]
            pushed = _record_pushes(client, monkeypatch)
            assert client.patch(f"/api/twin/incidents/{inc_id}", json={"status": "RESOLVED"}).status_code == 200
            assert len(pushed) == 1
            event, data = pushed[0]
            assert event == "incident" and data["incident_id"] == inc_id and data["status"] == "RESOLVED"
    finally:
        get_settings.cache_clear()
