# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Entry gate (live-valid door-entry decisions) and late-decided tailgating."""
from src.live.entry_gate import EntryGate
from src.rules.correlation import CorrelationEngine
from src.rules.engine import IncidentRuleEngine
from src.rules.policy import RestrictedZonePolicy
from tests.unit.test_correlation import _create_event, test_setup  # noqa: F401  (pytest fixture)

DOOR = {"cam_server_01": {"roi": [100, 100, 50, 100], "image_size": [640, 480], "min_sightings": 3}}
AT_DOOR, IN_ROOM = [110, 120, 30, 80], [400, 300, 30, 80]


def _gated(test_setup, tol=5.0):
    store, state, policy, access, topology, _corr, _rules = test_setup
    policy.correlation["grant_after_entry_tolerance_seconds"] = tol
    gate = EntryGate(DOOR)
    corr = CorrelationEngine(policy, access, topology, store, entry_filter=gate.entry_filter, entry_confirm=gate.entry_confirm)
    return gate, IncidentRuleEngine(store, state, policy, correlation=corr), policy


def _swipe(t, badge="badge_0001", person="person_it_admin_01"):
    ev = _create_event("ACCESS_GRANTED", t, f"badge:{badge}", zone_id="zone_server_room", camera_id="cam_server_01",
                       payload_dict={"door_id": "door_server_01", "badge_id": badge, "result": "GRANTED"})
    ev.person_id = person
    return ev


def _entry(track, t):
    return _create_event("ZONE_ENTERED", t, track, zone_id="zone_server_room", camera_id="cam_server_01")


def _tick(t):  # any later event lets the engine decide entries whose deadline passed
    return _create_event("PERSON_DETECTED", t, "trk_other", zone_id="zone_workspace", camera_id="cam_workspace_01")


def _types(incidents):
    return sorted((i.incident_type, i.track_id) for i in incidents)


def test_gate_checks_first_box_at_door_and_sightings():
    gate = EntryGate(DOOR)
    gate.observe("cam_server_01", "a", AT_DOOR, (640, 480))
    gate.observe("cam_server_01", "b", IN_ROOM, (640, 480))
    assert gate.entry_filter(_entry("a", "2026-01-15T10:00:00Z"))
    assert not gate.entry_filter(_entry("b", "2026-01-15T10:00:00Z"))   # appeared mid-room: re-acquired track
    assert not gate.entry_confirm(_entry("a", "2026-01-15T10:00:00Z"))  # 1 sighting < 3
    gate.observe("cam_server_01", "a", AT_DOOR, (640, 480)); gate.observe("cam_server_01", "a", AT_DOOR, (640, 480))
    assert gate.entry_confirm(_entry("a", "2026-01-15T10:00:00Z"))
    gate.observe("cam_server_01", "c", [220, 240, 60, 160], (1280, 960))  # other resolution: scaled to door's
    assert gate.entry_filter(_entry("c", "2026-01-15T10:00:00Z"))


def test_tracker_blip_at_door_does_not_use_up_the_badge(test_setup):
    gate, rules, _ = _gated(test_setup)
    gate.observe("cam_server_01", "blip", AT_DOOR, (640, 480))            # seen once, then lost
    for _ in range(3):
        gate.observe("cam_server_01", "real", AT_DOOR, (640, 480))
    out = rules.evaluate([_swipe("2026-01-15T10:00:00Z"), _entry("blip", "2026-01-15T10:00:01Z"),
                          _entry("real", "2026-01-15T10:00:02Z")])
    assert out == []                                                       # nothing decided before the deadline
    assert rules.evaluate([_tick("2026-01-15T10:00:08Z")]) == []           # blip dropped, real one used the badge


def test_unconfirmed_entry_without_badge_raises_nothing(test_setup):
    gate, rules, _ = _gated(test_setup)
    gate.observe("cam_server_01", "blip", AT_DOOR, (640, 480))
    rules.evaluate([_entry("blip", "2026-01-15T10:00:01Z")])
    assert rules.evaluate([_tick("2026-01-15T10:00:08Z")]) == []


def test_follower_decided_late_is_still_tailgating(test_setup):
    """The badge holder's grant must outlive the decision delay (bug: it was purged first)."""
    gate, rules, policy = _gated(test_setup)
    policy.restricted_zones["zone_server_room"] = RestrictedZonePolicy(authorized_person_ids=["person_it_admin_01"])
    for t in ("holder", "follower"):
        for _ in range(3):
            gate.observe("cam_server_01", t, AT_DOOR, (640, 480))
    rules.evaluate([_swipe("2026-01-15T10:00:00Z"), _entry("holder", "2026-01-15T10:00:01Z"),
                    _entry("follower", "2026-01-15T10:00:09Z")])
    out = rules.evaluate([_tick("2026-01-15T10:00:07Z")]) + rules.evaluate([_tick("2026-01-15T10:00:15Z")])
    assert _types(out) == [("TAILGATING", "follower"), ("UNAUTHORIZED_ENTRY", "follower")]


def test_ungated_late_decided_follower_is_tailgating(test_setup):
    """Same purge bug without a gate: entries wait `tol` for a late grant before being decided."""
    store, state, policy, access, topology, corr, rules = test_setup
    policy.correlation["grant_after_entry_tolerance_seconds"] = 5.0
    rules.evaluate([_swipe("2026-01-15T10:00:00Z"), _entry("holder", "2026-01-15T10:00:01Z"),
                    _entry("follower", "2026-01-15T10:00:09Z")])
    out = rules.evaluate([_tick("2026-01-15T10:00:15Z")])
    assert ("TAILGATING", "follower") in _types(out)
