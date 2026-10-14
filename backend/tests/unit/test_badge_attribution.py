"""Badge -> track identity attribution: a person admitted by a valid badge is not 'unauthorized'."""
from src.rules.policy import RestrictedZonePolicy
from tests.unit.test_correlation import _create_event, test_setup  # noqa: F401  (pytest fixture)

T0 = "2026-01-15T10:00:00Z"


def _restrict(policy, people):
    policy.restricted_zones["zone_server_room"] = RestrictedZonePolicy(authorized_person_ids=people)


def _swipe(badge, person):
    ev = _create_event("ACCESS_GRANTED", T0, f"badge:{badge}", zone_id="zone_server_room", camera_id="cam_server_01",
                       payload_dict={"door_id": "door_server_01", "badge_id": badge, "result": "GRANTED"})
    ev.person_id = person
    return ev


def _entry(track, ts="2026-01-15T10:00:05Z"):
    return _create_event("ZONE_ENTERED", ts, track, zone_id="zone_server_room", camera_id="cam_server_01")


def test_authorized_badge_holder_is_not_unauthorized(test_setup):
    store, state, policy, *_, rules = test_setup
    _restrict(policy, ["person_it_admin_01"])
    assert rules.evaluate([_swipe("badge_0001", "person_it_admin_01"), _entry("trk_a")]) == []


def test_valid_badge_of_unlisted_person_is_unauthorized_and_named(test_setup):
    store, state, policy, *_, rules = test_setup
    _restrict(policy, ["person_it_admin_01"])
    out = rules.evaluate([_swipe("badge_0002", "person_it_admin_02"), _entry("trk_b")])
    assert [(i.incident_type, i.person_id) for i in out] == [("UNAUTHORIZED_ENTRY", "person_it_admin_02")]


def test_unbadged_entry_is_unauthorized_plus_access_without_badge(test_setup):
    store, state, policy, *_, rules = test_setup
    _restrict(policy, ["person_it_admin_01"])
    out = rules.evaluate([_entry("trk_c")])
    assert sorted(i.incident_type for i in out) == ["ACCESS_WITHOUT_BADGE", "UNAUTHORIZED_ENTRY"]


def test_tailgater_is_unauthorized_but_badge_holder_is_not(test_setup):
    store, state, policy, *_, rules = test_setup
    _restrict(policy, ["person_it_admin_01"])
    out = rules.evaluate([_swipe("badge_0001", "person_it_admin_01"), _entry("trk_a"), _entry("trk_t", "2026-01-15T10:00:07Z")])
    assert sorted((i.incident_type, i.track_id) for i in out) == [("TAILGATING", "trk_t"), ("UNAUTHORIZED_ENTRY", "trk_t")]
