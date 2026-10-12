from fastapi.testclient import TestClient
from src.main import app


def test_twin_api_endpoints():
    """Verify /api/twin/state, /zones, /events, /incidents endpoints return valid schema responses."""
    with TestClient(app) as client:
        # 1. State
        res_state = client.get("/api/twin/state")
        assert res_state.status_code == 200
        state_data = res_state.json()
        assert "timestamp" in state_data
        assert "total_active_tracks" in state_data
        assert "zone_occupancy" in state_data
        assert "active_tracks" in state_data

        # 2. Zones
        res_zones = client.get("/api/twin/zones")
        assert res_zones.status_code == 200
        zones_data = res_zones.json()
        assert isinstance(zones_data, list)
        assert len(zones_data) >= 5
        zone_ids = {z["zone_id"] for z in zones_data}
        assert "zone_reception" in zone_ids
        assert "zone_server_room" in zone_ids

        # Verify bounding boxes are populated (Patch 2)
        reception = next(z for z in zones_data if z["zone_id"] == "zone_reception")
        assert reception["max_x"] > reception["min_x"]
        assert len(reception["vertices"]) >= 3
        assert reception["geometry_version"] >= 1

        # 3. Events
        res_events = client.get("/api/twin/events?limit=10")
        assert res_events.status_code == 200
        events_data = res_events.json()
        assert "total" in events_data
        assert "limit" in events_data
        assert "events" in events_data

        # 4. Incidents
        res_incidents = client.get("/api/twin/incidents")
        assert res_incidents.status_code == 200
        inc_data = res_incidents.json()
        assert "total" in inc_data
        assert "incidents" in inc_data
