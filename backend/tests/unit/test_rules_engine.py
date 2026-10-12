import json
import pytest

from src.rules.engine import IncidentRuleEngine
from src.rules.policy import AccessPolicy, BusinessHoursPolicy, RestrictedZonePolicy
from src.twin.migrations import run_migrations
from src.twin.models import TwinEvent, Zone
from src.twin.state import TwinState
from src.twin.store import TwinStore


@pytest.fixture
def store():
    st = TwinStore(":memory:")
    with st.get_connection() as conn:
        run_migrations(conn)

    # Insert test zones
    st.insert_zone(
        Zone(
            zone_id="zone_server_room",
            name="Server Room",
            zone_type="secure",
            security_level=4,
            max_occupancy=2,
        )
    )
    st.insert_zone(
        Zone(
            zone_id="zone_open_workspace",
            name="Workspace",
            zone_type="workspace",
            security_level=2,
            max_occupancy=10,
        )
    )
    st.insert_zone(
        Zone(
            zone_id="zone_reception",
            name="Reception",
            zone_type="public",
            security_level=1,
            max_occupancy=2,
        )
    )
    return st


@pytest.fixture
def policy():
    return AccessPolicy(
        version=1,
        restricted_zones={
            "zone_server_room": RestrictedZonePolicy(
                min_security_level=4,
                authorized_person_ids=["person_it_admin_01", "person_it_admin_02"],
            )
        },
        business_hours_utc=BusinessHoursPolicy(start="08:00", end="19:00"),
        dwell_alert_min_security_level=3,
    )


def test_over_capacity_resolves_on_cross_zone_transition(store, policy):
    """(a) Over-capacity resolves when a track moves directly from the zone into an adjacent zone."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    # Put 3 tracks in reception (max_occupancy = 2) -> over capacity
    state.update_track("trk_01", "cam_rec_01", "zone_reception", (2.0, 2.0), "2026-01-15T10:00:00Z")
    state.update_track("trk_02", "cam_rec_01", "zone_reception", (3.0, 3.0), "2026-01-15T10:00:00Z")
    state.update_track("trk_03", "cam_rec_01", "zone_reception", (4.0, 4.0), "2026-01-15T10:00:00Z")

    assert state.get_zone_occupancy("zone_reception") == 3

    # Trigger over-capacity via ZONE_ENTERED
    evt_enter = TwinEvent(
        event_id="evt_01",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_03",
        zone_id="zone_reception",
    )
    incidents = engine.evaluate([evt_enter])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.incident_type == "ZONE_OVER_CAPACITY"
    assert inc.status == "OPEN"
    assert inc.zone_id == "zone_reception"

    # Now track 3 moves directly from zone_reception to zone_open_workspace
    state.update_track("trk_03", "cam_ws_01", "zone_open_workspace", (15.0, 5.0), "2026-01-15T10:05:00Z")

    # Reception occupancy is now 2 (<= max_occupancy 2)
    assert state.get_zone_occupancy("zone_reception") == 2

    # Cross-zone transition emitted (zone_id=new zone, previous_zone_id=old zone)
    evt_cross = TwinEvent(
        event_id="evt_cross_01",
        event_type="CROSS_ZONE_TRANSITION",
        timestamp="2026-01-15T10:05:00Z",
        track_id="trk_03",
        zone_id="zone_open_workspace",
        previous_zone_id="zone_reception",
    )
    resolved_incidents = engine.evaluate([evt_cross])
    assert len(resolved_incidents) == 1
    res_inc = resolved_incidents[0]
    assert res_inc.incident_id == inc.incident_id
    assert res_inc.status == "RESOLVED"
    assert res_inc.end_time == "2026-01-15T10:05:00Z"
    assert "evt_cross_01" in json.loads(res_inc.supporting_event_ids_json)


def test_over_capacity_does_not_resolve_while_still_above_max(store, policy):
    """(b) Over-capacity does NOT resolve while occupancy is still above max."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    # Put 4 tracks in reception (max_occupancy = 2)
    for i in range(1, 5):
        state.update_track(f"trk_0{i}", "cam_rec_01", "zone_reception", (2.0 + i, 2.0), "2026-01-15T10:00:00Z")

    assert state.get_zone_occupancy("zone_reception") == 4

    evt_enter = TwinEvent(
        event_id="evt_04",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_04",
        zone_id="zone_reception",
    )
    incidents = engine.evaluate([evt_enter])
    assert len(incidents) == 1
    assert incidents[0].status == "OPEN"

    # One track transitions to workspace, but 3 remain in reception (3 > 2)
    state.update_track("trk_01", "cam_ws_01", "zone_open_workspace", (15.0, 5.0), "2026-01-15T10:02:00Z")
    assert state.get_zone_occupancy("zone_reception") == 3

    evt_cross = TwinEvent(
        event_id="evt_cross_02",
        event_type="CROSS_ZONE_TRANSITION",
        timestamp="2026-01-15T10:02:00Z",
        track_id="trk_01",
        zone_id="zone_open_workspace",
        previous_zone_id="zone_reception",
    )
    resolved = engine.evaluate([evt_cross])
    # Should NOT have resolved
    assert len(resolved) == 0

    open_incidents = store.get_incidents(status="OPEN")
    assert len(open_incidents) == 1
    assert open_incidents[0].status == "OPEN"


def test_investigating_incident_deduped_after_reinstantiating_engine(store, policy):
    """Verify an INVESTIGATING incident in the store is still deduped after re-instantiating the engine."""
    from src.twin.models import Incident

    # Store an existing incident with status INVESTIGATING
    inc = Incident(
        incident_id="inc_investigating_01",
        incident_type="UNAUTHORIZED_ENTRY",
        severity="HIGH",
        status="INVESTIGATING",
        zone_id="zone_server_room",
        track_id="trk_unauth_99",
        person_id=None,
        start_time="2026-01-15T09:00:00Z",
        supporting_event_ids_json=json.dumps(["evt_prior_01"]),
        summary="Track trk_unauth_99 (person: unknown) entered restricted zone zone_server_room.",
    )
    store.insert_incident(inc)

    # Re-instantiate engine
    fresh_state = TwinState()
    engine = IncidentRuleEngine(store, fresh_state, policy)

    # Trigger same unauthorized entry with a new event
    new_evt = TwinEvent(
        event_id="evt_new_02",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T09:05:00Z",
        track_id="trk_unauth_99",
        zone_id="zone_server_room",
        person_id=None,
    )
    evaluated = engine.evaluate([new_evt])

    # Should dedupe into the existing incident without creating a new one
    assert len(evaluated) == 1
    updated_inc = evaluated[0]
    assert updated_inc.incident_id == "inc_investigating_01"
    assert updated_inc.status == "INVESTIGATING"
    supporting = json.loads(updated_inc.supporting_event_ids_json)
    assert "evt_prior_01" in supporting
    assert "evt_new_02" in supporting

    # Verify store only has 1 incident total
    all_incidents = store.get_incidents()
    assert len(all_incidents) == 1
    assert all_incidents[0].incident_id == "inc_investigating_01"

