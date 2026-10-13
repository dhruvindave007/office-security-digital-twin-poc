"""Live M5 smoke test: real Bedrock investigation of a simulated tailgating incident (~$0.002).

Skips when no AWS credentials resolve (run scripts/aws_sso_login.py to sign in).
"""
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.simulator.scenarios import load_scenario


def test_live_tailgating_investigation_is_grounded_and_honest():
    with TestClient(app) as client:
        if app.state.bedrock_readiness.get("status") != "ready":
            pytest.skip(f"Live Bedrock skipped: {app.state.bedrock_readiness.get('credential_status')}")
        res = app.state.scenario_runner.run(load_scenario("../data/scenarios/tailgating.yaml"))
        store = app.state.twin_engine.store
        tg = next(i for i in res.incidents if store.get_incident(i).incident_type == "TAILGATING")

        body = client.post(f"/api/twin/incidents/{tg}/investigate",
                           json={"question": "Who was the second person?"}).json()
        print(f"\n[LIVE INVESTIGATION] status={body['status']} cost=${body['cost_usd']} "
              f"latency={body['latency_ms']}ms verification={body['verification']}")

        assert body["status"] == "COMPLETED"
        assert body["verification"]["passed"] is True, body["verification"]
        result = body["result"]
        assert "sim_trk_tail" in str(result)
        # the second track has no identity in the evidence: the model must not invent one
        assert any(w in result["person_identity"].lower() for w in ("not identified", "unknown", "not enough", "unidentified"))
        assert body["cost_usd"] < 0.01
