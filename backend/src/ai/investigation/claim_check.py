"""Claim-support verification: do the cited records actually say what each claim says?

Two stages, cheapest first:
  1. Deterministic (free): every entity (track, zone, camera, door, badge, person label) and every
     clock time a claim mentions must appear in the records it cites (or the shared context).
  2. LLM judge (one cheap call per investigation): each claim against only its cited records.
A claim is unsupported if EITHER stage says so. The deterministic stage never calls the model,
so it still protects the operator if Bedrock is unavailable.
"""
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    from src.ai.investigation.jsonutil import extract_json_object
    from src.ai.prompts import claim_check as prompt
except ImportError:
    from ai.investigation.jsonutil import extract_json_object
    from ai.prompts import claim_check as prompt

# (?<!\d)/(?!\d) instead of \b: in "T10:00:04" the "T" and "1" are both word characters, so \b
# never matches there and no record times would be read.
TIME_RE = re.compile(r"(?<!\d)(\d{2}):(\d{2}):(\d{2})(?!\d)")
VERDICTS = ("supported", "partially_supported", "unsupported")


def _record_entities(rec: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for key in ("track_id", "person_id", "camera_id", "zone_id", "previous_zone_id"):
        if rec.get(key):
            out.add(str(rec[key]))
    payload = rec.get("payload") or {}
    for key in ("door_id", "badge_id"):
        if payload.get(key):
            out.add(str(payload[key]))
    track = str(rec.get("track_id") or "")
    if track.startswith("badge:"):
        out.add(track.split(":", 1)[1])
    return out


def _seconds(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def _record_seconds(rec: Dict[str, Any]) -> Set[int]:
    out = set()
    for key in ("time", "start_time", "end_time"):
        value = rec.get(key)
        m = TIME_RE.search(str(value or ""))
        if m:
            out.add(_seconds(m.group(0)))
    return out


class ClaimExtractor:
    """Turns an investigation result into a flat list of checkable claims."""

    @staticmethod
    def extract(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        claims: List[Dict[str, Any]] = []
        for i, item in enumerate(result.get("timeline") or []):
            claims.append({"claim_id": f"timeline[{i}]", "kind": "observation", "text": str(item.get("description", "")),
                           "time": item.get("time"), "event_ids": list(item.get("event_ids") or [])})
        for i, item in enumerate(result.get("findings") or []):
            claims.append({"claim_id": f"findings[{i}]", "kind": item.get("kind", "observation"),
                           "text": str(item.get("statement", "")), "event_ids": list(item.get("event_ids") or [])})
        for key in ("summary", "answer"):
            if str(result.get(key) or "").strip():
                claims.append({"claim_id": key, "kind": "summary", "text": str(result[key]), "event_ids": []})
        return claims


class ClaimChecker:
    def __init__(self, bedrock_client=None, model_id: Optional[str] = None, effort: Optional[str] = "low",
                 max_output_tokens: int = 4000):
        self.bedrock = bedrock_client
        self.model_id = model_id
        self.effort = effort
        self.max_output_tokens = max_output_tokens

    # ---------------- stage 1: deterministic
    @staticmethod
    def deterministic_issues(claim: Dict[str, Any], evidence: Dict[str, Any]) -> List[str]:
        if claim["kind"] == "summary":
            return []  # whole-packet claims are left to the judge
        records = {e["event_id"]: e for e in evidence["events"]}
        for inc in [evidence["incident"], *evidence.get("related_incidents", [])]:
            records[inc["incident_id"]] = inc
        cited = [records[i] for i in claim["event_ids"] if i in records]

        all_entities: Set[str] = set()
        for rec in records.values():
            all_entities |= _record_entities(rec)
        allowed: Set[str] = set()
        for rec in cited:
            allowed |= _record_entities(rec)
        context = evidence.get("context") or {}
        allowed |= set(re.findall(r"[A-Za-z0-9_:.-]+", json.dumps(context)))

        issues = []
        text = claim["text"]
        mentioned = {e for e in all_entities if re.search(rf"(?<![\w:-]){re.escape(e)}(?![\w:-])", text)}
        for entity in sorted(mentioned - allowed):
            issues.append(f"mentions {entity}, which none of its cited records contain")

        cited_secs: Set[int] = set()
        for rec in cited:
            cited_secs |= _record_seconds(rec)
        times = [m.group(0) for m in TIME_RE.finditer(text)]
        if claim.get("time"):
            m = TIME_RE.search(str(claim["time"]))
            if m:
                times.append(m.group(0))
        for t in times:
            if cited_secs and not any(abs(_seconds(t) - s) <= 1 for s in cited_secs):
                issues.append(f"time {t} does not match any cited record")
        if not claim["event_ids"]:
            issues.append("cites no records")
        return issues

    # ---------------- stage 2: LLM judge
    def judge(self, claims: List[Dict[str, Any]], evidence: Dict[str, Any]):
        records = {e["event_id"]: e for e in evidence["events"]}
        for inc in [evidence["incident"], *evidence.get("related_incidents", [])]:
            records[inc["incident_id"]] = inc
        payload = [{"claim_id": c["claim_id"], "kind": c["kind"], "claim": c["text"],
                    **({"time": c["time"]} if c.get("time") else {}),
                    **({"auto_check_notes": c["deterministic_issues"]} if c.get("deterministic_issues") else {}),
                    "cited_records": [records[i] for i in c["event_ids"] if i in records]} for c in claims]
        is_claude = "anthropic" in (self.model_id or "")
        response = self.bedrock.converse(
            messages=[{"role": "user", "content": [{"text": prompt.build_user_message(
                json.dumps(payload, separators=(",", ":")), json.dumps(evidence, separators=(",", ":")))}]}],
            system_prompt=prompt.SYSTEM_PROMPT,
            model_id=self.model_id,
            max_tokens=self.max_output_tokens,
            temperature=None if is_claude else 0.0,
            additional_fields={"output_config": {"effort": self.effort}} if is_claude and self.effort else None,
        )
        return response

    def check(self, result: Dict[str, Any], evidence: Dict[str, Any], use_judge: bool = True) -> Dict[str, Any]:
        """Returns {"claims": [...], "passed", "unsupported", "partially_supported", "judge": {...}}."""
        claims = ClaimExtractor.extract(result)
        for c in claims:
            c["deterministic_issues"] = self.deterministic_issues(c, evidence)

        judge_info: Dict[str, Any] = {"ran": False}
        verdicts: Dict[str, Dict[str, str]] = {}
        if use_judge and self.bedrock is not None and claims:
            response = self.judge(claims, evidence)
            doc = extract_json_object(response.text) or {}
            for v in doc.get("verdicts") or []:
                if isinstance(v, dict) and v.get("claim_id") and v.get("verdict") in VERDICTS:
                    verdicts[v["claim_id"]] = {"verdict": v["verdict"], "reason": str(v.get("reason", ""))}
            judge_info = {"ran": True, "input_tokens": response.usage.input_tokens,
                          "output_tokens": response.usage.output_tokens, "latency_ms": round(response.latency_ms, 1),
                          "stop_reason": response.stop_reason, "prompt_version": prompt.PROMPT_VERSION,
                          "missing_verdicts": [c["claim_id"] for c in claims if c["claim_id"] not in verdicts]}

        out_claims = []
        for c in claims:
            j = verdicts.get(c["claim_id"])
            if j:
                # The judge sees the strict check's notes and decides. (Making them automatic fails
                # produced false alarms on absence claims, comparisons and policy facts.)
                final = j["verdict"]
            elif c["deterministic_issues"]:
                final = "unsupported"  # no judge available: the strict check is all we have
            else:
                final = "unchecked"  # judge did not run or gave no verdict for this claim
            out_claims.append({"claim_id": c["claim_id"], "kind": c["kind"], "text": c["text"],
                               "event_ids": c["event_ids"], "deterministic_issues": c["deterministic_issues"],
                               "judge_verdict": j["verdict"] if j else None, "judge_reason": j["reason"] if j else None,
                               "verdict": final})
        count = lambda v: sum(1 for c in out_claims if c["verdict"] == v)
        unchecked = count("unchecked")
        return {
            "claims": out_claims,
            "unsupported": count("unsupported"),
            "partially_supported": count("partially_supported"),
            "unchecked": unchecked,
            # "passed" only when every claim was actually checked and none is unsupported
            "passed": count("unsupported") == 0 and unchecked == 0,
            "judge": judge_info,
        }
