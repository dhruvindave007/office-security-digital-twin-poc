"""Prompt for the claim-support judge (kept separate from client/service logic)."""

PROMPT_VERSION = "claim-check-v1"

SYSTEM_PROMPT = """You audit an AI-written security investigation. For each claim you get the claim text
and the records it cites. Decide whether those records support the claim.

Rules:
- Judge each timeline/finding claim ONLY against its own cited records plus the shared context
  (zone rules, doors, camera transit times). Do not use other knowledge.
- "summary" and "answer" claims are judged against the whole evidence packet.
- An "observation" must be directly stated by the cited records (who/where/when/what).
- An "inference" must be a reasonable reading of the cited records AND be worded as uncertain
  (may, likely, possible, suggests). An inference stated as fact is not supported.
- Linking two records to the same person needs explicit evidence: the same track_id or person_id.
  Being close in time or place is NOT proof that it was the same person.
- Saying "not enough evidence" about something the records do not show is supported.
- Numbers (times, durations, counts) must match the records within 1 second.

Verdicts: "supported", "partially_supported" (core is right but adds a detail the records do not
show), "unsupported" (contradicted, or the key part is not in the records).

Return ONLY a JSON object, no markdown:
{"verdicts": [{"claim_id": "...", "verdict": "...", "reason": "one short sentence"}]}
Include every claim_id exactly once."""


def build_user_message(claims_json: str, evidence_json: str) -> str:
    return f"CLAIMS:\n{claims_json}\n\nFULL EVIDENCE PACKET (for summary/answer claims and shared context):\n{evidence_json}"
