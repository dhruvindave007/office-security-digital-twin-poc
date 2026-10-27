# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import json
from datetime import datetime
import pytest

from src.access.config import AccessConfig, Door
from src.rules.correlation import CorrelationEngine, IncidentCandidate
from src.rules.engine import IncidentRuleEngine
from src.rules.policy import AccessPolicy, CorrelationPolicy
from src.twin.engine import generate_uuid7
from src.twin.models import Camera, Incident, TwinEvent, Zone
from src.twin.state import TwinState
from src.twin.store import TwinStore
from src.vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode
from src.vision.topology import TopologyService


@pytest.fixture
def test_setup():
    """Build a complete test fixture with TwinStore, Topology, AccessConfig, and CorrelationEngine."""
    store = TwinStore(":memory:")

    # Setup zones
    store.insert_zone(
        Zone(
            zone_id="zone_server_room",
            name="Server Room",
            zone_type="restricted",
            security_level=4,
        )
    )
    store.insert_zone(
        Zone(
            zone_id="zone_workspace",
            name="Workspace",
            zone_type="workspace",
            security_level=2,
        )
    )

    # Setup cameras
    store.insert_camera(
        Camera(
            camera_id="cam_server_01",
            name="Server Camera",
            primary_zone_id="zone_server_room",
        )
    )
    store.insert_camera(
        Camera(
            camera_id="cam_workspace_01",
            name="Workspace Camera",
            primary_zone_id="zone_workspace",
        )
    )
    store.insert_camera(
        Camera(
            camera_id="cam_isolated_01",
            name="Isolated Camera",
            primary_zone_id="zone_workspace",
        )
    )

    # Topology: cam_workspace_01 <-> cam_server_01 (min 4.0s)
    graph = CameraAdjacencyGraph()
    graph.add_node(CameraNode(camera_id="cam_workspace_01", name="Workspace"))
    graph.add_node(CameraNode(camera_id="cam_server_01", name="Server"))
    graph.add_node(CameraNode(camera_id="cam_isolated_01", name="Isolated"))
    graph.add_edge(
        CameraEdge(
            "cam_workspace_01",
            "cam_server_01",
            distance_meters=12.0,
            min_transit_seconds=4.0,
            max_transit_seconds=20.0,
            bidirectional=True,
        )
    )
    topology = TopologyService(graph=graph)

    # Policy
    policy = AccessPolicy(
        version=1,
        restricted_zones={},
        correlation=CorrelationPolicy(
            badge_entry_window_seconds=15.0,
            tailgating_window_seconds=10.0,
            transit_slack_seconds=2.0,
        ),
    )

    # Access Config
    access_config = AccessConfig(
        doors={
            "door_server_01": Door(
                door_id="door_server_01",
                name="Server Door",
                zone_id="zone_server_room",
                camera_id="cam_server_01",
                badge_required=True,
            )
        },
        badges={
            "badge_0001": "person_it_admin_01",
            "badge_0002": "person_it_admin_02",
        },
    )

    state = TwinState()
    corr_engine = CorrelationEngine(
        policy=policy,
        access_config=access_config,
        topology=topology,
        store=store,
    )
    rule_engine = IncidentRuleEngine(
        store=store,
        state=state,
        policy=policy,
        correlation=corr_engine,
    )

    return store, state, policy, access_config, topology, corr_engine, rule_engine


def _create_event(
    event_type: str,
    timestamp: str,
    track_id: str,
    zone_id: str = None,
    camera_id: str = None,
    payload_dict: dict = None,
) -> TwinEvent:
    return TwinEvent(
        event_id=generate_uuid7(),
        event_type=event_type,
        timestamp=timestamp,
        track_id=track_id,
        zone_id=zone_id,
        camera_id=camera_id,
        confidence=1.0,
        payload_json=json.dumps(payload_dict or {}),
        created_at=timestamp,
    )


def test_badge_then_timely_entry_produces_no_incident(test_setup):
    """Positive case: Badge swipe at t=0s followed by entry at t=5s (within 15s window) produces no incident."""
    *_, rule_engine = test_setup

    swipe = _create_event(
        "ACCESS_GRANTED",
        "2026-01-15T10:00:00Z",
        "badge:badge_0001",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
        payload_dict={"door_id": "door_server_01", "badge_id": "badge_0001", "result": "GRANTED"},
    )
    entry = _create_event(
        "ZONE_ENTERED",
        "2026-01-15T10:00:05Z",
        "trk_admin_01",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
    )

    incidents = rule_engine.evaluate([swipe, entry])
    assert len(incidents) == 0


def test_entry_outside_badge_window_produces_access_without_badge(test_setup):
    """Negative case: Entry at window + 1s (t=16s) produces ACCESS_WITHOUT_BADGE."""
    *_, rule_engine = test_setup

    swipe = _create_event(
        "ACCESS_GRANTED",
        "2026-01-15T10:00:00Z",
        "badge:badge_0001",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
        payload_dict={"door_id": "door_server_01", "badge_id": "badge_0001", "result": "GRANTED"},
    )
    # Entry at t=16s (past 15s window)
    entry = _create_event(
        "ZONE_ENTERED",
        "2026-01-15T10:00:16Z",
        "trk_visitor_01",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
    )

    incidents = rule_engine.evaluate([swipe, entry])
    # The sweep at t=16s also expires the grant (BADGE_WITHOUT_ENTRY) and entry produces ACCESS_WITHOUT_BADGE
    types = {inc.incident_type for inc in incidents}
    assert "ACCESS_WITHOUT_BADGE" in types
    assert "BADGE_WITHOUT_ENTRY" in types

    awb = [inc for inc in incidents if inc.incident_type == "ACCESS_WITHOUT_BADGE"][0]
    assert awb.severity == "MEDIUM"
    assert awb.track_id == "trk_visitor_01"


def test_second_track_inside_tailgating_window_produces_tailgating(test_setup):
    """Positive case for TAILGATING: Track 2 enters 3s after Track 1 used a grant (window 10s)."""
    *_, rule_engine = test_setup

    swipe = _create_event(
        "ACCESS_GRANTED",
        "2026-01-15T10:00:00Z",
        "badge:badge_0001",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
        payload_dict={"door_id": "door_server_01", "badge_id": "badge_0001", "result": "GRANTED"},
    )
    entry1 = _create_event(
        "ZONE_ENTERED",
        "2026-01-15T10:00:02Z",
        "trk_first",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
    )
    # Second track enters at t=5s (3s after first track used grant)
    entry2 = _create_event(
        "ZONE_ENTERED",
        "2026-01-15T10:00:05Z",
        "trk_second",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
    )

    incidents = rule_engine.evaluate([swipe, entry1, entry2])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.incident_type == "TAILGATING"
    assert inc.severity == "HIGH"
    assert inc.track_id == "trk_second"
    # Evidence includes swipe event_id
    sup_ids = json.loads(inc.supporting_event_ids_json)
    assert swipe.event_id in sup_ids
    assert entry2.event_id in sup_ids


def test_second_track_outside_tailgating_window_produces_access_without_badge(test_setup):
    """Negative case for TAILGATING: Track 2 enters 12s after Track 1 (outside 10s tailgating window)."""
    *_, rule_engine = test_setup

    swipe = _create_event(
        "ACCESS_GRANTED",
        "2026-01-15T10:00:00Z",
        "badge:badge_0001",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
        payload_dict={"door_id": "door_server_01", "badge_id": "badge_0001", "result": "GRANTED"},
    )
    entry1 = _create_event(
        "ZONE_ENTERED",
        "2026-01-15T10:00:02Z",
        "trk_first",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
    )
    # Second track enters at t=15s (13s after first track entered -> exceeds 10s tailgating window)
    entry2 = _create_event(
        "ZONE_ENTERED",
        "2026-01-15T10:00:15Z",
        "trk_second",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
    )

    incidents = rule_engine.evaluate([swipe, entry1, entry2])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.incident_type == "ACCESS_WITHOUT_BADGE"
    assert inc.severity == "MEDIUM"
    assert inc.track_id == "trk_second"


def test_grant_expiry_sweep_and_dedupe_on_second_grant(test_setup):
    """Verify grant sweep on subsequent event triggers BADGE_WITHOUT_ENTRY and dedupes second expired grant."""
    *_, rule_engine = test_setup

    swipe1 = _create_event(
        "ACCESS_GRANTED",
        "2026-01-15T10:00:00Z",
        "badge:badge_0002",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
        payload_dict={"door_id": "door_server_01", "badge_id": "badge_0002", "result": "GRANTED"},
    )
    rule_engine.evaluate([swipe1])

    # Unrelated event arriving 30s later triggers the sweep
    unrelated = _create_event(
        "PERSON_DETECTED",
        "2026-01-15T10:00:30Z",
        "trk_unrelated",
        zone_id="zone_workspace",
        camera_id="cam_workspace_01",
    )
    incidents = rule_engine.evaluate([unrelated])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.incident_type == "BADGE_WITHOUT_ENTRY"
    assert inc.severity == "LOW"
    assert inc.track_id == "badge:badge_0002"

    # Second swipe for same badge expires too
    swipe2 = _create_event(
        "ACCESS_GRANTED",
        "2026-01-15T10:01:00Z",
        "badge:badge_0002",
        zone_id="zone_server_room",
        camera_id="cam_server_01",
        payload_dict={"door_id": "door_server_01", "badge_id": "badge_0002", "result": "GRANTED"},
    )
    rule_engine.evaluate([swipe2])
    unrelated2 = _create_event(
        "PERSON_DETECTED",
        "2026-01-15T10:01:30Z",
        "trk_unrelated",
        zone_id="zone_workspace",
        camera_id="cam_workspace_01",
    )
    incidents2 = rule_engine.evaluate([unrelated2])
    assert len(incidents2) == 1
    # Evidence appended to same incident (deduplicated)
    assert incidents2[0].incident_id == inc.incident_id
    sup_ids = json.loads(incidents2[0].supporting_event_ids_json)
    assert swipe1.event_id in sup_ids
    assert swipe2.event_id in sup_ids


def test_transit_feasibility_rules(test_setup):
    """Verify TRACK_DISCONTINUITY: too fast raises, slow does not, same camera does not, unreachable raises."""
    *_, rule_engine = test_setup

    # 1. Detection on workspace at t=0
    det1 = _create_event(
        "PERSON_DETECTED",
        "2026-01-15T10:00:00Z",
        "trk_jump",
        camera_id="cam_workspace_01",
    )
    rule_engine.evaluate([det1])

    # 2. Too fast: workspace -> server min is 4.0s, slack 2.0s -> allowed threshold is >= 2.0s.
    # Detection at t=1.0s (elapsed 1.0s < 2.0s) -> raises TRACK_DISCONTINUITY
    det_fast = _create_event(
        "PERSON_DETECTED",
        "2026-01-15T10:00:01Z",
        "trk_jump",
        camera_id="cam_server_01",
    )
    incidents_fast = rule_engine.evaluate([det_fast])
    assert len(incidents_fast) == 1
    assert incidents_fast[0].incident_type == "TRACK_DISCONTINUITY"
    assert incidents_fast[0].severity == "LOW"

    # 3. Same camera at t=2.0s does not raise
    det_same = _create_event(
        "PERSON_DETECTED",
        "2026-01-15T10:00:02Z",
        "trk_jump",
        camera_id="cam_server_01",
    )
    incidents_same = rule_engine.evaluate([det_same])
    assert len(incidents_same) == 0

    # 4. Valid transit duration: server -> workspace at t=10.0s (elapsed 8.0s >= 2.0s) does not raise
    det_slow = _create_event(
        "PERSON_DETECTED",
        "2026-01-15T10:00:10Z",
        "trk_jump",
        camera_id="cam_workspace_01",
    )
    incidents_slow = rule_engine.evaluate([det_slow])
    assert len(incidents_slow) == 0

    # 5. Unreachable camera: workspace -> isolated camera has no topology path
    det_unreachable = _create_event(
        "PERSON_DETECTED",
        "2026-01-15T10:00:20Z",
        "trk_jump",
        camera_id="cam_isolated_01",
    )
    incidents_unreach = rule_engine.evaluate([det_unreachable])
    assert len(incidents_unreach) == 1
    assert incidents_unreach[0].incident_type == "TRACK_DISCONTINUITY"


def test_forbidden_words_not_in_any_summary(test_setup):
    """Verify forbidden words (suspicious, malicious, intruder, threat, attacker) never appear in summaries."""
    *_, rule_engine = test_setup

    events = [
        # ACCESS_WITHOUT_BADGE
        _create_event("ZONE_ENTERED", "2026-01-15T10:00:00Z", "trk_1", zone_id="zone_server_room", camera_id="cam_server_01"),
        # Swipe for TAILGATING
        _create_event("ACCESS_GRANTED", "2026-01-15T10:00:01Z", "badge:badge_0001", zone_id="zone_server_room", camera_id="cam_server_01", payload_dict={"door_id": "door_server_01", "badge_id": "badge_0001", "result": "GRANTED"}),
        _create_event("ZONE_ENTERED", "2026-01-15T10:00:02Z", "trk_2", zone_id="zone_server_room", camera_id="cam_server_01"),
        # Tailgater
        _create_event("ZONE_ENTERED", "2026-01-15T10:00:04Z", "trk_3", zone_id="zone_server_room", camera_id="cam_server_01"),
        # Transit discontinuity
        _create_event("PERSON_DETECTED", "2026-01-15T10:00:05Z", "trk_4", camera_id="cam_workspace_01"),
        _create_event("PERSON_DETECTED", "2026-01-15T10:00:06Z", "trk_4", camera_id="cam_server_01"),
        # Badge without entry sweep trigger
        _create_event("ACCESS_GRANTED", "2026-01-15T10:00:10Z", "badge:badge_0002", zone_id="zone_server_room", camera_id="cam_server_01", payload_dict={"door_id": "door_server_01", "badge_id": "badge_0002", "result": "GRANTED"}),
        _create_event("PERSON_DETECTED", "2026-01-15T10:00:50Z", "trk_5", camera_id="cam_workspace_01"),
    ]

    incidents = rule_engine.evaluate(events)
    assert len(incidents) >= 4

    forbidden_words = ["suspicious", "malicious", "intruder", "threat", "attacker"]
    for inc in incidents:
        text = inc.summary.lower()
        for word in forbidden_words:
            assert word not in text, f"Forbidden word '{word}' found in summary: '{inc.summary}'"


def test_fact_5_new_incident_types_deduped_after_engine_restart(test_setup):
    """Verify Fact 5: ACCESS_WITHOUT_BADGE, TAILGATING, BADGE_WITHOUT_ENTRY, TRACK_DISCONTINUITY reload into dedupe on restart."""
    store, state, policy, access_config, topology, _, rule_engine = test_setup

    now_iso = "2026-01-15T10:00:00Z"
    # Seed 1 open incident of each new type in the store
    inc_awb = Incident(
        incident_id="inc_awb",
        incident_type="ACCESS_WITHOUT_BADGE",
        severity="MEDIUM",
        status="OPEN",
        zone_id="zone_server_room",
        track_id="trk_dedupe_1",
        start_time=now_iso,
        summary="Initial AWB",
    )
    inc_tail = Incident(
        incident_id="inc_tail",
        incident_type="TAILGATING",
        severity="HIGH",
        status="OPEN",
        zone_id="zone_server_room",
        track_id="trk_dedupe_2",
        start_time=now_iso,
        summary="Initial Tailgating",
    )
    inc_bwe = Incident(
        incident_id="inc_bwe",
        incident_type="BADGE_WITHOUT_ENTRY",
        severity="LOW",
        status="OPEN",
        zone_id="zone_server_room",
        track_id="badge:badge_dedupe",
        start_time=now_iso,
        summary="Initial BWE",
    )
    inc_disc = Incident(
        incident_id="inc_disc",
        incident_type="TRACK_DISCONTINUITY",
        severity="LOW",
        status="OPEN",
        track_id="trk_dedupe_3",
        start_time=now_iso,
        summary="Initial Discontinuity",
    )

    for inc in [inc_awb, inc_tail, inc_bwe, inc_disc]:
        store.insert_incident(inc)

    # Re-instantiate engine (simulating restart)
    corr2 = CorrelationEngine(policy, access_config, topology, store)
    reloaded_engine = IncidentRuleEngine(store, state, policy, correlation=corr2)

    # Trigger events for the same dedupe keys
    # 1. AWB for trk_dedupe_1 (no grants available)
    e1 = _create_event("ZONE_ENTERED", "2026-01-15T10:01:00Z", "trk_dedupe_1", zone_id="zone_server_room", camera_id="cam_server_01")
    res1 = reloaded_engine.evaluate([e1])
    assert len(res1) == 1
    assert res1[0].incident_id == "inc_awb"

    # 2. Tailgating for trk_dedupe_2 (grant used by another track 3s prior)
    grant_used = {
        "event_id": "grant_x",
        "timestamp": datetime.fromisoformat("2026-01-15T10:01:05+00:00"),
        "timestamp_str": "2026-01-15T10:01:05Z",
        "person_id": None,
        "badge_id": "badge_0001",
        "used_by_track": "trk_other",
        "used_at": datetime.fromisoformat("2026-01-15T10:01:07+00:00"),
        "grant_event": e1,
    }
    corr2.pending_grants["door_server_01"] = [grant_used]
    e2 = _create_event("ZONE_ENTERED", "2026-01-15T10:01:10Z", "trk_dedupe_2", zone_id="zone_server_room", camera_id="cam_server_01")
    res2 = reloaded_engine.evaluate([e2])
    assert len(res2) == 1
    assert res2[0].incident_id == "inc_tail"

    # 3. Discontinuity for trk_dedupe_3
    corr2.last_seen["trk_dedupe_3"] = ("cam_workspace_01", datetime.fromisoformat("2026-01-15T10:01:15+00:00"), "evt_prior")
    e3 = _create_event("PERSON_DETECTED", "2026-01-15T10:01:16Z", "trk_dedupe_3", camera_id="cam_server_01")
    res3 = reloaded_engine.evaluate([e3])
    assert len(res3) == 1
    assert res3[0].incident_id == "inc_disc"

    # 4. BWE for badge_dedupe (grant expired 20s ago)
    grant_expire = {
        "event_id": "grant_bwe",
        "timestamp": datetime.fromisoformat("2026-01-15T10:01:00+00:00"),
        "timestamp_str": "2026-01-15T10:01:00Z",
        "person_id": None,
        "badge_id": "badge_dedupe",
        "used_by_track": None,
        "used_at": None,
        "grant_event": _create_event("ACCESS_GRANTED", "2026-01-15T10:01:00Z", "badge:badge_dedupe"),
    }
    corr2.pending_grants["door_server_01"].append(grant_expire)
    e4 = _create_event("PERSON_DETECTED", "2026-01-15T10:01:30Z", "trk_other", camera_id="cam_workspace_01")
    res4 = reloaded_engine.evaluate([e4])
    assert len(res4) == 1
    assert res4[0].incident_id == "inc_bwe"

    # Store total incidents count must still be 4
    all_stored = store.get_incidents()
    assert len(all_stored) == 4
    ids = {inc.incident_id for inc in all_stored}
    assert ids == {"inc_awb", "inc_tail", "inc_bwe", "inc_disc"}
