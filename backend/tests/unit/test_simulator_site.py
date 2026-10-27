# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Scenarios on a real site whose cameras have no pixel homography (WiseNET room-level cameras)."""
import os
from datetime import datetime, timezone

from src.rules.engine import IncidentRuleEngine
from src.rules.policy import load_access_policy
from src.simulator.runner import ScenarioRunner
from src.simulator.scenarios import load_scenario
from src.twin.bootstrap import TwinBootstrapService
from src.vision.calibration import HomographyManager
from src.vision.pipeline import ObservationPipeline
from src.vision.zones import VisionZoneResolver

SITE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "sites", "wisenet")


def test_wisenet_scenario_runs_now_on_room_level_camera(tmp_path):
    eng = TwinBootstrapService.bootstrap(db_path=str(tmp_path / "s.db"), seed_path=os.path.join(SITE, "floorplan_seed.yaml"))
    rules = IncidentRuleEngine(eng.store, eng.state, load_access_policy(os.path.join(SITE, "access_policy.yaml")))
    hm = HomographyManager()
    pipe = ObservationPipeline(hm, VisionZoneResolver.from_twin_store(eng.store), eng, rule_engine=rules)
    before = datetime.now(timezone.utc)
    res = ScenarioRunner(pipe, hm).run(load_scenario(os.path.join(SITE, "scenarios", "lab_unauthorized_visitor.yaml")))
    assert res.errors == [] and res.steps_processed == 3
    # Outside business hours AFTER_HOURS_BREACH is raised too, so look for the entry incident by type.
    incs = [eng.store.get_incident(i) for i in res.incidents]
    inc = next(i for i in incs if i.incident_type == "UNAUTHORIZED_ENTRY")
    assert inc.zone_id == "zone_vision_lab"
    started = datetime.fromisoformat(inc.start_time.replace("Z", "+00:00"))
    assert (started - before).total_seconds() >= -1      # played at the current time ("now")
    eng.store.close()
