# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
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
    assert res_inc.end_time == "2026-01-15T10:05:00.000000Z"
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


def test_prune_once_resolves_over_capacity(store, policy):
    """Verify that _prune_once resolves an open over-capacity incident when tracks are pruned."""
    from src.main import _prune_once
    from src.twin.engine import DigitalTwinEngine
    from src.twin.geometry import GeometryEngine

    from src.twin.models import Camera

    store.insert_camera(Camera(camera_id="cam_reception_01", name="Reception Camera", primary_zone_id="zone_reception"))

    zones = store.get_zones()
    verts_map = {
        "zone_reception": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        "zone_open_workspace": [(10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0)],
        "zone_server_room": [(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0)],
    }
    geom = GeometryEngine(zones=zones, vertices_by_zone=verts_map)
    state = TwinState()
    twin_engine = DigitalTwinEngine(store=store, state=state, geometry=geom, track_ttl_seconds=30.0)
    rule_engine = IncidentRuleEngine(store, state, policy)

    # Reception max occupancy is 2 in policy.
    # Ingest 3 tracks at t0 -> over capacity
    t0 = "2026-01-15T10:00:00Z"
    for i in range(1, 4):
        evts = twin_engine.ingest_observation(
            track_id=f"trk_occ_{i}",
            camera_id="cam_reception_01",
            x=5.0,
            y=5.0,
            timestamp=t0,
        )
        rule_engine.evaluate(evts)

    # Confirm over-capacity incident is OPEN
    incidents = store.get_incidents(status="OPEN")
    assert len(incidents) == 1
    assert incidents[0].incident_type == "ZONE_OVER_CAPACITY"

    # Advance time past TTL (30s) to 40s later
    t1 = "2026-01-15T10:00:40Z"
    _prune_once(twin_engine, rule_engine, t1)

    # Occupancy is now 0 <= 2, incident should be RESOLVED
    inc = store.get_incident(incidents[0].incident_id)
    assert inc is not None
    assert inc.status == "RESOLVED"
    assert inc.end_time is not None


def test_unauthorized_entry_rule_positive_and_negative(store, policy):
    """Verify Rule 1: Positive on unlisted/None person, Negative on authorized person."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    # 1. Negative: authorized IT admin enters server room -> NO incident
    evt_auth = TwinEvent(
        event_id="evt_auth_01",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_admin_01",
        person_id="person_it_admin_01",
        zone_id="zone_server_room",
    )
    incidents = engine.evaluate([evt_auth])
    assert len(incidents) == 0
    assert len(store.get_incidents()) == 0

    # 2. Positive: unauthorized person (person_id=None) enters server room
    evt_unauth = TwinEvent(
        event_id="evt_unauth_01",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:05:00Z",
        track_id="trk_visitor_01",
        person_id=None,
        zone_id="zone_server_room",
    )
    incidents = engine.evaluate([evt_unauth])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.incident_type == "UNAUTHORIZED_ENTRY"
    assert inc.status == "OPEN"
    assert inc.severity == "HIGH"
    assert inc.start_time == "2026-01-15T10:05:00.000000Z"
    assert inc.created_at == "2026-01-15T10:05:00.000000Z"


def test_zone_over_capacity_negative_when_occupancy_equals_max(store, policy):
    """Verify Rule 2 Negative: occupancy == max_occupancy does not trigger ZONE_OVER_CAPACITY."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    # Reception max occupancy is 2. Set exactly 2 tracks.
    state.update_track("trk_01", "cam_rec_01", "zone_reception", (2.0, 2.0), "2026-01-15T10:00:00Z")
    state.update_track("trk_02", "cam_rec_01", "zone_reception", (3.0, 3.0), "2026-01-15T10:00:00Z")
    assert state.get_zone_occupancy("zone_reception") == 2

    evt_enter = TwinEvent(
        event_id="evt_occ_exact_01",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_02",
        zone_id="zone_reception",
    )
    incidents = engine.evaluate([evt_enter])
    assert len(incidents) == 0
    assert len(store.get_incidents()) == 0


def test_dwell_anomaly_positive_and_negative(store, policy):
    """Verify Rule 3: Dwell anomaly triggers only at/above dwell_alert_min_security_level."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    # 1. Negative: DWELL_TIME_EXCEEDED in workspace (security_level = 2 < 3) -> NO incident
    evt_ws_dwell = TwinEvent(
        event_id="evt_ws_dwell_01",
        event_type="DWELL_TIME_EXCEEDED",
        timestamp="2026-01-15T10:10:00Z",
        track_id="trk_ws_01",
        zone_id="zone_open_workspace",
    )
    incidents = engine.evaluate([evt_ws_dwell])
    assert len(incidents) == 0

    # 2. Positive: DWELL_TIME_EXCEEDED in server room (security_level = 4 >= 3) -> DWELL_ANOMALY
    evt_srv_dwell = TwinEvent(
        event_id="evt_srv_dwell_01",
        event_type="DWELL_TIME_EXCEEDED",
        timestamp="2026-01-15T10:15:00Z",
        track_id="trk_srv_01",
        zone_id="zone_server_room",
    )
    incidents = engine.evaluate([evt_srv_dwell])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.incident_type == "DWELL_ANOMALY"
    assert inc.status == "OPEN"
    assert inc.severity == "MEDIUM"
    assert inc.start_time == "2026-01-15T10:15:00.000000Z"
    assert inc.created_at == "2026-01-15T10:15:00.000000Z"


def test_after_hours_breach_positive_and_boundary_negatives(store, policy):
    """Verify Rule 4: Boundary timestamps 08:00:00 and 19:00:00 do NOT trigger, but 20:30:00 triggers."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    # 1. Boundary Negative: 08:00:00 exactly
    evt_morning_boundary = TwinEvent(
        event_id="evt_0800",
        event_type="PERSON_DETECTED",
        timestamp="2026-01-15T08:00:00Z",
        track_id="trk_bound_01",
        zone_id="zone_open_workspace",
    )
    assert len(engine.evaluate([evt_morning_boundary])) == 0

    # 2. Boundary Negative: 19:00:00 exactly
    evt_evening_boundary = TwinEvent(
        event_id="evt_1900",
        event_type="PERSON_DETECTED",
        timestamp="2026-01-15T19:00:00Z",
        track_id="trk_bound_02",
        zone_id="zone_open_workspace",
    )
    assert len(engine.evaluate([evt_evening_boundary])) == 0

    # 3. Positive: 20:30:00 (outside 08:00 - 19:00)
    evt_night = TwinEvent(
        event_id="evt_2030",
        event_type="PERSON_DETECTED",
        timestamp="2026-01-15T20:30:00Z",
        track_id="trk_night_01",
        zone_id="zone_open_workspace",
    )
    incidents = engine.evaluate([evt_night])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.incident_type == "AFTER_HOURS_BREACH"
    assert inc.status == "OPEN"
    assert inc.severity == "LOW"
    assert inc.start_time == "2026-01-15T20:30:00.000000Z"
    assert inc.created_at == "2026-01-15T20:30:00.000000Z"


def test_second_matching_event_appends_to_supporting_events(store, policy):
    """Verify second matching event appends to supporting_event_ids without duplicate incident."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    evt1 = TwinEvent(
        event_id="evt_dup_01",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_unauth_88",
        zone_id="zone_server_room",
    )
    res1 = engine.evaluate([evt1])
    assert len(res1) == 1
    inc_id = res1[0].incident_id

    evt2 = TwinEvent(
        event_id="evt_dup_02",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:02:00Z",
        track_id="trk_unauth_88",
        zone_id="zone_server_room",
    )
    res2 = engine.evaluate([evt2])
    assert len(res2) == 1
    assert res2[0].incident_id == inc_id

    # Check store has 1 incident with both supporting events
    all_inc = store.get_incidents()
    assert len(all_inc) == 1
    supporting = json.loads(all_inc[0].supporting_event_ids_json)
    assert supporting == ["evt_dup_01", "evt_dup_02"]


def test_after_forget_or_resolve_new_incident_is_created(store, policy):
    """Verify that after forgetting/resolving an incident, the same situation creates a NEW incident."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    evt1 = TwinEvent(
        event_id="evt_init_01",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="trk_rec_reuse",
        zone_id="zone_server_room",
    )
    res1 = engine.evaluate([evt1])
    assert len(res1) == 1
    first_id = res1[0].incident_id

    # Forget the incident (as done when patched to RESOLVED/FALSE_POSITIVE)
    engine.forget(first_id)

    # Same situation triggers again
    evt2 = TwinEvent(
        event_id="evt_after_forget_02",
        event_type="ZONE_ENTERED",
        timestamp="2026-01-15T10:10:00Z",
        track_id="trk_rec_reuse",
        zone_id="zone_server_room",
    )
    res2 = engine.evaluate([evt2])
    assert len(res2) == 1
    second_id = res2[0].incident_id
    assert second_id != first_id


def test_incident_summaries_contain_no_alarmist_words(store, policy):
    """Verify that incident summaries never contain suspicious, malicious, intruder, or threat."""
    state = TwinState()
    engine = IncidentRuleEngine(store, state, policy)

    # 1. UNAUTHORIZED_ENTRY
    evt_unauth = TwinEvent("e1", "ZONE_ENTERED", "2026-01-15T10:00:00Z", "trk_1", zone_id="zone_server_room")
    # 2. ZONE_OVER_CAPACITY
    state.update_track("trk_1", "cam_rec_01", "zone_reception", (1.0, 1.0), "2026-01-15T10:00:00Z")
    state.update_track("trk_2", "cam_rec_01", "zone_reception", (2.0, 2.0), "2026-01-15T10:00:00Z")
    state.update_track("trk_3", "cam_rec_01", "zone_reception", (3.0, 3.0), "2026-01-15T10:00:00Z")
    evt_cap = TwinEvent("e2", "ZONE_ENTERED", "2026-01-15T10:00:00Z", "trk_3", zone_id="zone_reception")
    # 3. DWELL_ANOMALY
    evt_dwell = TwinEvent("e3", "DWELL_TIME_EXCEEDED", "2026-01-15T10:00:00Z", "trk_1", zone_id="zone_server_room")
    # 4. AFTER_HOURS_BREACH
    evt_night = TwinEvent("e4", "PERSON_DETECTED", "2026-01-15T22:00:00Z", "trk_1", zone_id="zone_open_workspace")

    incidents = engine.evaluate([evt_unauth, evt_cap, evt_dwell, evt_night])
    assert len(incidents) == 4

    forbidden = {"suspicious", "malicious", "intruder", "threat"}
    for inc in incidents:
        summary_lower = inc.summary.lower()
        for word in forbidden:
            assert word not in summary_lower, f"Forbidden word '{word}' found in summary: '{inc.summary}'"


