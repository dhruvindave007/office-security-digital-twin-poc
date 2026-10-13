"""Incident investigation: evidence -> Bedrock (Converse) -> verified, audited result.

Bedrock never touches the real-time path: this runs on demand for one incident. The model's
output is NOT trusted blindly: citations are checked against the evidence packet, banned
wording is flagged, and every call (including failures) is stored with tokens and cost.
"""
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional

try:
    from src.ai.errors import BedrockError
    from src.ai.investigation.evidence import EvidenceBuilder
    from src.ai.pricing import estimate_cost_usd
    from src.ai.prompts import investigation as prompt
    from src.twin.engine import generate_uuid7
except ImportError:
    from ai.errors import BedrockError
    from ai.investigation.evidence import EvidenceBuilder
    from ai.pricing import estimate_cost_usd
    from ai.prompts import investigation as prompt
    from twin.engine import generate_uuid7

logger = logging.getLogger("office_vision.ai.investigation")

REQUIRED_KEYS = ("summary", "timeline", "findings", "person_identity", "benign_explanations",
                 "missing_evidence", "recommended_actions", "confidence")
BANNED_WORDS = ("malicious", "suspicious", "intruder", "threat", "attacker")
ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class IncidentNotFoundError(KeyError):
    pass


class BudgetExceededError(RuntimeError):
    pass


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Parse the model's JSON object, tolerating a markdown fence or stray prose around it."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for candidate in (cleaned, (re.search(r"\{.*\}", cleaned, re.S) or [None])[0]):
        if not candidate:
            continue
        try:
            doc = json.loads(candidate)
            if isinstance(doc, dict):
                return doc
        except (TypeError, ValueError):
            continue
    return None


def verify_result(result: Optional[Dict[str, Any]], evidence: Dict[str, Any], stop_reason: str) -> Dict[str, Any]:
    """Machine checks on the model output. passed=False means a human must not trust it as-is."""
    # Legitimate = any ID the model was actually given, anywhere in the packet (events, incidents,
    # and e.g. related incidents' supporting_event_ids). Anything else was invented or mis-copied.
    valid_ids = set(ID_PATTERN.findall(json.dumps(evidence)))
    if result is None:
        return {"passed": False, "json_valid": False, "missing_keys": list(REQUIRED_KEYS), "cited_ids": 0,
                "unverified_ids": [], "banned_words": [], "truncated": stop_reason == "max_tokens"}
    flat = json.dumps(result)
    cited = set(ID_PATTERN.findall(flat))
    unverified = sorted(cited - valid_ids)
    banned = sorted({w for w in BANNED_WORDS if re.search(rf"\b{w}\b", flat, re.I)})
    missing = [k for k in REQUIRED_KEYS if k not in result]
    return {
        "passed": not unverified and not banned and not missing and stop_reason != "max_tokens",
        "json_valid": True,
        "missing_keys": missing,
        "cited_ids": len(cited & valid_ids),
        "unverified_ids": unverified,       # IDs the model cited that are NOT in the evidence
        "banned_words": banned,
        "truncated": stop_reason == "max_tokens",
    }


class InvestigationService:
    def __init__(self, bedrock_client, store, evidence_builder: EvidenceBuilder, model_id: str,
                 max_output_tokens: int = 6000, daily_budget_usd: float = 1.0, effort: Optional[str] = "low"):
        self.bedrock = bedrock_client
        self.store = store
        self.evidence_builder = evidence_builder
        self.model_id = model_id
        self.max_output_tokens = max_output_tokens
        self.daily_budget_usd = daily_budget_usd
        self.effort = effort

    def spent_today_usd(self) -> float:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.store.sum_investigation_cost_since(midnight.isoformat())

    def investigate(self, incident_id: str, question: str = "") -> Dict[str, Any]:
        evidence = self.evidence_builder.build(incident_id)
        if evidence is None:
            raise IncidentNotFoundError(incident_id)
        spent = self.spent_today_usd()
        if spent >= self.daily_budget_usd:
            raise BudgetExceededError(
                f"Daily investigation budget reached (${spent:.4f} of ${self.daily_budget_usd:.2f}).")

        record: Dict[str, Any] = {
            "investigation_id": generate_uuid7(),
            "incident_id": incident_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model_id": self.model_id,
            "question": question or prompt.DEFAULT_QUESTION,
            "evidence_event_count": len(evidence["events"]),
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 0.0,
            "result_json": None, "raw_output": None, "error": None,
        }
        # Claude 5.x on Bedrock rejects `temperature` and is steered with `effort` instead
        # (Converse additionalModelRequestFields); other models get deterministic output.
        is_claude = "anthropic" in self.model_id
        temperature = None if is_claude else 0.0
        extra = {"output_config": {"effort": self.effort}} if is_claude and self.effort else None
        try:
            response = self.bedrock.converse(
                messages=[{"role": "user", "content": [{"text": prompt.build_user_message(
                    json.dumps(evidence, separators=(",", ":")), question)}]}],
                system_prompt=prompt.SYSTEM_PROMPT,
                model_id=self.model_id,
                max_tokens=self.max_output_tokens,
                temperature=temperature,
                additional_fields=extra,
            )
        except BedrockError as e:
            record.update(status="FAILED", error=f"{type(e).__name__}: {e}",
                          verification_json=json.dumps({"passed": False}))
            self.store.insert_investigation(record)
            raise

        result = extract_json_object(response.text)
        verification = verify_result(result, evidence, response.stop_reason)
        cost = estimate_cost_usd(self.model_id, response.usage.input_tokens, response.usage.output_tokens)
        record.update(
            status="COMPLETED" if result is not None else "INVALID_OUTPUT",
            result_json=json.dumps(result) if result is not None else None,
            raw_output=None if result is not None else response.text[:20000],
            verification_json=json.dumps({**verification, "prompt_version": prompt.PROMPT_VERSION,
                                          "effort": self.effort if is_claude else None}),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=cost if cost is not None else 0.0,
            latency_ms=round(response.latency_ms, 1),
        )
        if cost is None:
            logger.warning("No price recorded for model %s; investigation cost stored as 0.", self.model_id)
        self.store.insert_investigation(record)
        return to_view(record)


def to_view(record: Dict[str, Any]) -> Dict[str, Any]:
    """API representation of a stored investigation row."""
    return {
        "investigation_id": record["investigation_id"],
        "incident_id": record["incident_id"],
        "created_at": record["created_at"],
        "status": record["status"],
        "model_id": record["model_id"],
        "question": record.get("question"),
        "result": json.loads(record["result_json"]) if record.get("result_json") else None,
        "verification": json.loads(record.get("verification_json") or "{}"),
        "evidence_event_count": record.get("evidence_event_count", 0),
        "usage": {"input_tokens": record.get("input_tokens", 0), "output_tokens": record.get("output_tokens", 0)},
        "cost_usd": record.get("cost_usd", 0.0),
        "latency_ms": record.get("latency_ms", 0.0),
        "error": record.get("error"),
        "raw_output": record.get("raw_output"),
    }
