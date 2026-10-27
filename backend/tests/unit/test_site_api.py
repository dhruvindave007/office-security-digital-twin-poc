# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""GET /api/site and the event_ids filter used by the dashboard."""
from fastapi.testclient import TestClient

from src.main import app


def test_site_lists_real_cameras_and_doors():
    with TestClient(app) as client:
        r = client.get("/api/site")
        assert r.status_code == 200
        d = r.json()
        assert {c["camera_id"] for c in d["cameras"]} == {c.camera_id for c in app.state.twin_engine.store.get_cameras()}
        assert all("zone_id" in c and "positioning" in c for c in d["cameras"])
        assert isinstance(d["doors"], list) and any("not match people across cameras" in n for n in d["notes"])


def test_events_by_ids_returns_exactly_those_events():
    with TestClient(app) as client:
        res = client.post("/api/simulator/scenarios/tailgating/run").json()
        inc = client.get(f"/api/twin/incidents/{res['incidents'][0]}").json()
        ids = inc["supporting_event_ids"]
        evs = client.get("/api/twin/events", params={"event_ids": ",".join(ids)}).json()
        assert {e["event_id"] for e in evs["events"]} == set(ids) and evs["total"] == len(ids)
        assert client.get("/api/twin/events", params={"event_ids": ",".join(["x"] * 201)}).status_code == 422
