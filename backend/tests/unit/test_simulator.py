from pathlib import Path
import pytest

from src.access.config import load_access_config
from src.access.service import AccessControlService
from src.rules.correlation import CorrelationEngine
from src.rules.engine import IncidentRuleEngine
from src.rules.policy import load_access_policy
from src.simulator.runner import ScenarioRunner
from src.simulator.scenarios import Scenario, ScenarioStep, list_scenarios, load_scenario
from src.twin.bootstrap import TwinBootstrapService
from src.twin.engine import DigitalTwinEngine
from src.twin.state import TwinState
from src.vision.pipeline import ObservationPipeline
from src.vision.seed import VisionSeedLoader
from src.vision.zones import VisionZoneResolver


def _create_test_pipeline(tmp_path: Path):
    """Create a fully wired ObservationPipeline and AccessControlService backed by a temporary SQLite database."""
    db_path = str(tmp_path / "test_twin.db")
    twin_engine = TwinBootstrapService.bootstrap(
        db_path=db_path,
        seed_path="src/twin/floorplan_seed.yaml",
    )

    homography_mgr, camera_graph, topology = VisionSeedLoader.load_all()
    zone_resolver = VisionZoneResolver.from_twin_store(twin_engine.store)
    policy = load_access_policy("data/rules/access_policy.yaml")
    access_config = load_access_config("data/access/doors.yaml")

    correlation_engine = CorrelationEngine(
        policy=policy,
        access_config=access_config,
        topology=topology,
        store=twin_engine.store,
    )
    rule_engine = IncidentRuleEngine(
        twin_engine.store,
        twin_engine.state,
        policy,
        correlation=correlation_engine,
    )

    access_service = AccessControlService(
        store=twin_engine.store,
        config=access_config,
        rule_engine=rule_engine,
    )

    pipeline = ObservationPipeline(
        homography_manager=homography_mgr,
        zone_resolver=zone_resolver,
        twin_engine=twin_engine,
        topology_service=topology,
        rule_engine=rule_engine,
    )
    return pipeline, homography_mgr, twin_engine, access_service


def test_scenario_loader_validation():
    """Verify ScenarioStep and Scenario validation rules for observation and access steps."""
    # Valid observation step
    step = ScenarioStep(t=0.0, camera_id="cam_01", track_id="trk_01", floor=[5.0, 5.0])
    assert step.t == 0.0
    assert step.kind == "observation"

    # Observation step: empty camera_id
    with pytest.raises(ValueError, match="camera_id cannot be empty"):
        ScenarioStep(t=0.0, camera_id="", track_id="trk_01", floor=[5.0, 5.0])

    # Observation step: empty track_id
    with pytest.raises(ValueError, match="track_id cannot be empty"):
        ScenarioStep(t=0.0, camera_id="cam_01", track_id="", floor=[5.0, 5.0])

    # Observation step: bad floor (too few numbers)
    with pytest.raises(ValueError, match="floor coordinates must be a list of 2 numbers"):
        ScenarioStep(t=0.0, camera_id="cam_01", track_id="trk_01", floor=[5.0])

    # Observation step: bad floor (too many numbers)
    with pytest.raises(ValueError, match="floor coordinates must be a list of 2 numbers"):
        ScenarioStep(t=0.0, camera_id="cam_01", track_id="trk_01", floor=[5.0, 5.0, 5.0])

    # Valid access step
    acc_step = ScenarioStep(
        t=2.0,
        kind="access",
        door_id="door_server_01",
        badge_id="badge_0001",
        result="GRANTED",
    )
    assert acc_step.kind == "access"
    assert acc_step.result == "GRANTED"

    # Access step: empty door_id
    with pytest.raises(ValueError, match="door_id cannot be empty"):
        ScenarioStep(t=0.0, kind="access", door_id="", badge_id="badge_01", result="GRANTED")

    # Access step: empty badge_id
    with pytest.raises(ValueError, match="badge_id cannot be empty"):
        ScenarioStep(t=0.0, kind="access", door_id="door_01", badge_id="", result="GRANTED")

    # Access step: invalid result
    with pytest.raises(ValueError, match="result must be 'GRANTED' or 'DENIED'"):
        ScenarioStep(t=0.0, kind="access", door_id="door_01", badge_id="badge_01", result="UNKNOWN")

    # Unknown step kind
    with pytest.raises(ValueError, match="Unknown scenario step kind"):
        ScenarioStep(t=0.0, kind="teleport", door_id="door_01")

    # Unsorted steps in Scenario
    s1 = ScenarioStep(t=10.0, camera_id="cam_01", track_id="trk_01", floor=[5.0, 5.0])
    s2 = ScenarioStep(t=5.0, camera_id="cam_01", track_id="trk_01", floor=[5.0, 5.0])
    with pytest.raises(ValueError, match="must be sorted by time offset 't'"):
        Scenario(name="bad_order", description="", base_time_utc="2026-01-15T10:00:00Z", steps=[s1, s2])


def test_list_and_load_scenarios():
    """Verify discovery and loading of all shipped scenarios (4 existing + 3 new)."""
    scenarios = list_scenarios("data/scenarios")
    names = {s.name for s in scenarios}
    assert len(scenarios) == 7
    assert "normal_workday" in names
    assert "server_room_unauthorized_entry" in names
    assert "reception_over_capacity" in names
    assert "after_hours_presence" in names
    assert "tailgating" in names
    assert "badge_without_entry" in names
    assert "impossible_transit" in names


def test_scenario_normal_workday(tmp_path):
    """Verify normal_workday with authorized badge swipe produces exactly 0 incidents."""
    pipeline, homography_mgr, _, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/normal_workday.yaml")
    result = runner.run(scenario)

    assert result.errors == []
    assert result.steps_processed == len(scenario.steps)
    assert len(result.incidents) == 0


def test_scenario_server_room_unauthorized_entry(tmp_path):
    """Verify server_room_unauthorized_entry produces UNAUTHORIZED_ENTRY, DWELL_ANOMALY, and ACCESS_WITHOUT_BADGE."""
    pipeline, homography_mgr, twin_engine, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/server_room_unauthorized_entry.yaml")
    result = runner.run(scenario)

    assert result.errors == []
    assert result.steps_processed == len(scenario.steps)
    assert len(result.incidents) == 3

    incidents = twin_engine.store.get_incidents()
    types = {inc.incident_type for inc in incidents}
    assert types == {"UNAUTHORIZED_ENTRY", "DWELL_ANOMALY", "ACCESS_WITHOUT_BADGE"}
    assert len(incidents) == 3


def test_scenario_reception_over_capacity(tmp_path):
    """Verify reception_over_capacity produces exactly 1 ZONE_OVER_CAPACITY and zero other incidents."""
    pipeline, homography_mgr, twin_engine, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/reception_over_capacity.yaml")
    result = runner.run(scenario)

    assert result.errors == []
    assert result.steps_processed == 16
    assert len(result.incidents) == 1

    incidents = twin_engine.store.get_incidents()
    assert len(incidents) == 1
    assert incidents[0].incident_type == "ZONE_OVER_CAPACITY"
    assert incidents[0].zone_id == "zone_reception"


def test_scenario_after_hours_presence(tmp_path):
    """Verify after_hours_presence produces exactly 1 AFTER_HOURS_BREACH and zero other incidents."""
    pipeline, homography_mgr, twin_engine, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/after_hours_presence.yaml")
    result = runner.run(scenario)

    assert result.errors == []
    assert result.steps_processed == 3
    assert len(result.incidents) == 1

    incidents = twin_engine.store.get_incidents()
    assert len(incidents) == 1
    assert incidents[0].incident_type == "AFTER_HOURS_BREACH"
    assert incidents[0].track_id == "sim_trk_night_01"


def test_scenario_tailgating(tmp_path):
    """Verify tailgating scenario produces TAILGATING and UNAUTHORIZED_ENTRY on sim_trk_tail."""
    pipeline, homography_mgr, twin_engine, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/tailgating.yaml")
    result = runner.run(scenario)

    assert result.errors == []
    assert result.steps_processed == len(scenario.steps)
    assert len(result.incidents) == 2

    incidents = twin_engine.store.get_incidents()
    types = {inc.incident_type for inc in incidents}
    assert types == {"TAILGATING", "UNAUTHORIZED_ENTRY"}
    for inc in incidents:
        assert inc.track_id == "sim_trk_tail"


def test_scenario_badge_without_entry(tmp_path):
    """Verify badge_without_entry scenario produces BADGE_WITHOUT_ENTRY when sweep fires."""
    pipeline, homography_mgr, twin_engine, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/badge_without_entry.yaml")
    result = runner.run(scenario)

    assert result.errors == []
    assert result.steps_processed == len(scenario.steps)
    assert len(result.incidents) == 1

    incidents = twin_engine.store.get_incidents()
    assert incidents[0].incident_type == "BADGE_WITHOUT_ENTRY"


def test_scenario_impossible_transit(tmp_path):
    """Verify impossible_transit scenario produces TRACK_DISCONTINUITY."""
    pipeline, homography_mgr, twin_engine, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/impossible_transit.yaml")
    result = runner.run(scenario)

    assert result.errors == []
    assert result.steps_processed == len(scenario.steps)
    assert len(result.incidents) == 1

    incidents = twin_engine.store.get_incidents()
    assert incidents[0].incident_type == "TRACK_DISCONTINUITY"
    assert incidents[0].track_id == "sim_trk_jump"


def test_scenario_determinism(tmp_path):
    """Verify running the same scenario twice on fresh databases yields identical event counts."""
    dir1 = tmp_path / "run1"
    dir1.mkdir()
    pipeline1, homography_mgr1, _, access1 = _create_test_pipeline(dir1)
    runner1 = ScenarioRunner(pipeline1, homography_mgr1, access_service=access1)

    dir2 = tmp_path / "run2"
    dir2.mkdir()
    pipeline2, homography_mgr2, _, access2 = _create_test_pipeline(dir2)
    runner2 = ScenarioRunner(pipeline2, homography_mgr2, access_service=access2)

    scenario = load_scenario("data/scenarios/server_room_unauthorized_entry.yaml")
    res1 = runner1.run(scenario)
    res2 = runner2.run(scenario)

    assert res1.events_emitted == res2.events_emitted
    assert res1.event_type_counts == res2.event_type_counts
    assert len(res1.incidents) == len(res2.incidents)
    assert res1.errors == res2.errors == []

    # Assert identical incident types
    types1 = [inc.incident_type for inc in pipeline1.twin_engine.store.get_incidents()]
    types2 = [inc.incident_type for inc in pipeline2.twin_engine.store.get_incidents()]
    assert types1 == types2


def test_scenario_replay_safety(tmp_path):
    """Verify replay safety: replaying ledger events on a re-instantiated engine leaves incidents unchanged."""
    pipeline, homography_mgr, twin_engine, access_service = _create_test_pipeline(tmp_path)
    runner = ScenarioRunner(pipeline, homography_mgr, access_service=access_service)

    scenario = load_scenario("data/scenarios/server_room_unauthorized_entry.yaml")
    result = runner.run(scenario)
    assert len(result.incidents) == 3

    initial_incidents = twin_engine.store.get_incidents()
    initial_count = len(initial_incidents)
    assert initial_count == 3

    # Build fresh TwinState and DigitalTwinEngine on the exact same database store
    fresh_state = TwinState()
    replay_engine = DigitalTwinEngine(
        store=twin_engine.store,
        state=fresh_state,
        geometry=twin_engine.geometry,
    )
    all_events, _ = twin_engine.store.get_events(limit=500)
    all_events_asc = sorted(all_events, key=lambda e: e.sequence_num)

    # Replay all events
    replay_engine.replay_events(all_events_asc)

    # Store incidents must remain unchanged
    replayed_incidents = replay_engine.store.get_incidents()
    assert len(replayed_incidents) == initial_count
