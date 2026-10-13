"""M5 incident investigation: evidence, verification, budget, audit, API (fake Bedrock, no network)."""
import json

import pytest
from fastapi.testclient import TestClient

from src.ai.bedrock_client import _inference_config
from src.ai.errors import BedrockThrottlingError
from src.ai.investigation.service import (
    BudgetExceededError, IncidentNotFoundError, extract_json_object, verify_result,
)
from src.ai.pricing import estimate_cost_usd
from src.ai.types import BedrockConverseResponse, TokenUsage
from src.main import app
from src.simulator.scenarios import load_scenario


class FakeBedrock:
    """Records calls; replies with a canned investigation citing the given IDs."""

    def __init__(self, cite=None, text=None, stop_reason="end_turn", raise_exc=None):
        self.calls, self.cite, self.text, self.stop_reason, self.raise_exc = [], cite, text, stop_reason, raise_exc

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise self.raise_exc
        text = self.text if self.text is not None else json.dumps({
            "summary": "A track entered the restricted zone.", "timeline": [{"time": "t", "description": "d", "event_ids": self.cite or []}],
            "findings": [{"statement": "s", "kind": "observation", "event_ids": self.cite or []}],
            "person_identity": "not enough evidence", "benign_explanations": ["b"], "missing_evidence": ["m"],
            "recommended_actions": ["r"], "confidence": "medium", "answer": ""})
        return BedrockConverseResponse(text=text, model_id=kwargs["model_id"], region="us-east-1", request_id="r",
                                       latency_ms=12.0, usage=TokenUsage(input_tokens=4000, output_tokens=600, total_tokens=4600),
                                       stop_reason=self.stop_reason, streamed=False, timestamp="t", content_blocks=[])


def _run_tailgating():
    res = app.state.scenario_runner.run(load_scenario("../data/scenarios/tailgating.yaml"))
    store = app.state.twin_engine.store
    return {store.get_incident(i).incident_type: store.get_incident(i) for i in res.incidents}


def test_evidence_includes_every_involved_track_and_context():
    with TestClient(app):
        inc = _run_tailgating()["TAILGATING"]
        ev = app.state.investigation_service.evidence_builder.build(inc.incident_id)
        tracks = {e["track_id"] for e in ev["events"]}
        # the badge holder's track must be there, or the model cannot explain tailgating
        assert {"sim_trk_tail", "sim_trk_admin", "badge:badge_0001"} <= tracks
        assert set(json.loads(inc.supporting_event_ids_json)) <= {e["event_id"] for e in ev["events"]}
        assert ev["context"]["zone"]["zone_id"] == "zone_server_room"
        assert ev["context"]["policy"]["zone_is_restricted"] is True
        assert ev["context"]["doors"][0]["door_id"] == "door_server_01"
        assert {r["type"] for r in ev["related_incidents"]} == {"UNAUTHORIZED_ENTRY"}
        assert ev["events_truncated"] is False


def test_evidence_for_unknown_incident_is_none():
    with TestClient(app):
        assert app.state.investigation_service.evidence_builder.build("nope") is None


def test_extract_json_handles_fences_and_prose():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Here you go: {"a": 2} hope it helps') == {"a": 2}
    assert extract_json_object("no json here") is None


def test_verify_flags_invented_ids_banned_words_and_truncation():
    ev = {"incident": {"incident_id": "11111111-1111-7111-8111-111111111111"}, "related_incidents": [],
          "events": [{"event_id": "22222222-2222-7222-8222-222222222222"}]}
    keys = {k: [] for k in ("timeline", "findings", "benign_explanations", "missing_evidence", "recommended_actions")}
    good = {"summary": "ok 22222222-2222-7222-8222-222222222222", "person_identity": "x", "confidence": "low", **keys}
    assert verify_result(good, ev, "end_turn")["passed"] is True
    invented = dict(good, summary="see 33333333-3333-7333-8333-333333333333")
    v = verify_result(invented, ev, "end_turn")
    assert v["passed"] is False and v["unverified_ids"] == ["33333333-3333-7333-8333-333333333333"]
    v = verify_result(dict(good, summary="a Suspicious person"), ev, "end_turn")
    assert v["passed"] is False and v["banned_words"] == ["suspicious"]
    assert verify_result(good, ev, "max_tokens")["passed"] is False
    # an ID that appears anywhere in the packet (here: a related incident's supporting event) is legitimate
    ev_related = dict(ev, related_incidents=[{"incident_id": "44444444-4444-7444-8444-444444444444",
                                              "supporting_event_ids": ["55555555-5555-7555-8555-555555555555"]}])
    cites_related = dict(good, summary="see 55555555-5555-7555-8555-555555555555")
    assert verify_result(cites_related, ev_related, "end_turn")["unverified_ids"] == []
    assert verify_result(None, ev, "end_turn")["json_valid"] is False


def test_investigation_success_is_verified_costed_and_audited():
    with TestClient(app) as client:
        inc = _run_tailgating()["TAILGATING"]
        svc = app.state.investigation_service
        svc.bedrock = FakeBedrock(cite=json.loads(inc.supporting_event_ids_json))
        body = client.post(f"/api/twin/incidents/{inc.incident_id}/investigate", json={"question": "Who is it?"}).json()
        assert body["status"] == "COMPLETED"
        assert body["verification"]["passed"] is True
        assert body["verification"]["cited_ids"] == 2
        assert body["cost_usd"] == pytest.approx(estimate_cost_usd(svc.model_id, 4000, 600))
        assert body["question"] == "Who is it?"
        call = svc.bedrock.calls[0]
        assert call["model_id"] == "global.anthropic.claude-haiku-5-5"
        assert call["temperature"] is None            # Claude 5.x rejects temperature
        assert call["additional_fields"] == {"output_config": {"effort": "low"}}
        assert call["max_tokens"] == 6000
        assert body["verification"]["effort"] == "low"
        assert "Who is it?" in call["messages"][0]["content"][0]["text"]
        listed = client.get(f"/api/twin/incidents/{inc.incident_id}/investigations").json()
        assert [i["investigation_id"] for i in listed["investigations"]] == [body["investigation_id"]]
        assert listed["spent_today_usd"] == pytest.approx(body["cost_usd"])


def test_invalid_model_output_is_stored_not_trusted():
    with TestClient(app) as client:
        inc = _run_tailgating()["TAILGATING"]
        app.state.investigation_service.bedrock = FakeBedrock(text="I think it was fine.")
        body = client.post(f"/api/twin/incidents/{inc.incident_id}/investigate").json()
        assert body["status"] == "INVALID_OUTPUT"
        assert body["result"] is None and body["raw_output"] == "I think it was fine."
        assert body["verification"]["passed"] is False


def test_bedrock_failure_is_recorded_and_returns_502():
    with TestClient(app) as client:
        inc = _run_tailgating()["TAILGATING"]
        app.state.investigation_service.bedrock = FakeBedrock(raise_exc=BedrockThrottlingError("slow down"))
        res = client.post(f"/api/twin/incidents/{inc.incident_id}/investigate")
        assert res.status_code == 502
        rows = app.state.twin_engine.store.get_investigations(inc.incident_id)
        assert rows[0]["status"] == "FAILED" and "slow down" in rows[0]["error"]


def test_daily_budget_blocks_further_calls():
    with TestClient(app) as client:
        inc = _run_tailgating()["TAILGATING"]
        svc = app.state.investigation_service
        svc.bedrock = FakeBedrock(cite=[])
        svc.daily_budget_usd = 0.0005           # one fake call costs ~0.0007
        assert client.post(f"/api/twin/incidents/{inc.incident_id}/investigate").status_code == 200
        res = client.post(f"/api/twin/incidents/{inc.incident_id}/investigate")
        assert res.status_code == 429 and "budget" in res.json()["detail"].lower()
        assert len(svc.bedrock.calls) == 1      # the blocked request never reached Bedrock


def test_unknown_incident_is_404_and_service_raises():
    with TestClient(app) as client:
        assert client.post("/api/twin/incidents/nope/investigate").status_code == 404
        assert client.get("/api/twin/incidents/nope/investigations").status_code == 404
        with pytest.raises(IncidentNotFoundError):
            app.state.investigation_service.investigate("nope")


def test_inference_config_omits_temperature_when_none():
    assert _inference_config(100, None) == {"maxTokens": 100}
    assert _inference_config(100, 0.0) == {"maxTokens": 100, "temperature": 0.0}


def test_cost_is_none_for_unpriced_model():
    assert estimate_cost_usd("some.unknown-model", 1000, 1000) is None
    assert estimate_cost_usd("global.anthropic.claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
