# Investigation eval 20261014T091001Z

Cases: 27 · models: haiku, sonnet, nova-lite · judge: global.anthropic.claude-haiku-5-5 · spent: $1.3492

| Model | Mean score | All checks passed | Unsupported claims | Errors | Mean cost | Mean seconds |
|---|---|---|---|---|---|---|
| haiku | 0.936 | 19/27 | 6 | 0 | $0.0026 | 17.4 |
| sonnet | 0.964 | 22/27 | 4 | 0 | $0.0464 | 17.8 |
| nova-lite | 0.905 | 15/27 | 6 | 0 | $0.0010 | 10.7 |

## Method (read before quoting any number)

**What a case is.** Each of the 27 cases replays one synthetic scenario into a fresh twin, picks one
incident, and asks the investigator one question (`backend/evals/investigation/cases.yaml`).
Categories: incident 7, identity 5, out-of-scope 4, numeric 3, ambiguous 2, policy 2, sensor error 2,
missing evidence 1, conflicting evidence 1. The cases were written by the same author (Claude) who built
the system, to cover known failure modes (guessing identity, intent, missing evidence). They are **not**
a random sample of real incidents, and no case uses real footage.

**What is checked (automatically, per answer).** Only the checks that apply to a case are counted:
- `valid_json`: the model returned the required structure.
- `ids_exist`: every event/incident/track ID it cites exists in the evidence packet (citation validity).
- `claims_supported`: **claim support, not just citations.** Factual claims are extracted and checked
  against the cited records, first by deterministic hints (times, IDs, entities), then by a Claude
  Haiku 5.5 judge (prompt claim-check-v2). Any unsupported claim fails the check.
- `neutral_wording`: no banned accusatory words.
- `mentions`: required facts (e.g. the tailgater's track and the badge) appear.
- `hedges`: the answer admits uncertainty when, and only when, the evidence cannot settle the question.
- `identity`: says "not identified", or names the person *and* calls it a system label.
- `number`: the right count appears. `no_forbidden_claim`: no precise identity assertion the evidence lacks.

**What the score means.** "Mean score" is partial credit: the fraction of applicable checks passed,
averaged over cases. It flatters the model. The stricter figure is **"All checks passed"**: Haiku
answered 19 of 27 (70%) with every check passing (20 of 27 after the 2026-10-15 scorer correction below), and 6 of its 27 answers contained at least one
unsupported claim (all of these were caught and flagged by the verifier, which is how the system
behaves live: an answer with unsupported claims is marked as failing verification).

**Known weaknesses.** Haiku judges Haiku's claims (self-judging bias possible; not yet
cross-checked with a different judge model). The checks are keyword/regex based apart from the judge.
Human review of `investigation_eval_2026-10-14.csv` has not been done yet. Treat the result as a
regression baseline for comparing models and prompts, not as a measured accuracy on real incidents.

## Update 2026-10-15: scorer correction and prompt v2

**Correction.** The scorer's uncertainty pattern (`UNCERTAIN_RE` in `run_eval.py`) contained literal
backspace characters instead of `` from the day it was written, so it never matched; only the phrase
list detected hedges. Fixed (with tests) and the saved answers above re-scored without new model calls:
Haiku 0.936 -> **0.942, 20/27 all checks passed**; Nova Lite 0.905 -> **0.911, 16/27**; Sonnet unchanged
(0.964, 22/27). One verdict changed per affected model (`it_label`). The table above is kept as published.

**Prompt investigation-v2** (every timeline item and finding must carry an ID; related incidents are
cited by their incident_id). Reason: on live WiseNET incidents, answers failed verification on claims
that were true but uncited. Case `e2_first` was updated: since badge attribution the first track is
linked to the badge holder and is no longer flagged, so the case now targets the follower.

| Model | Prompt | Mean score | All checks passed | Unsupported claims | Mean cost |
|---|---|---|---|---|---|
| haiku | v1 (2026-10-14, corrected) | 0.942 | 20/27 | 6 | $0.0026 |
| haiku | v2 (2026-10-15, `results/20261015T144446Z`) | 0.993 | 26/27 | 0 | $0.0027 |

Single run each; model answers vary between runs, so treat small differences with care. The remaining
failure (`e6_escalate`, missing a required mention) failed for all three models under v1 as well.

## Failed checks by case (2026-10-14 run, prompt v1)

- tg_real / nova-lite: mentions
- tg_who / nova-lite: claims_supported
- tg_unauth_why / nova-lite: mentions
- sr_path / nova-lite: mentions
- sr_dwell_len / nova-lite: claims_supported
- sr_dwell_doing / haiku: claims_supported
- sr_dwell_doing / nova-lite: hedges
- sr_nobadge / nova-lite: mentions
- it_real / nova-lite: hedges
- it_label / haiku: hedges
- it_label / nova-lite: hedges
- ah_breach / sonnet: claims_supported
- bwe_entered / haiku: claims_supported
- bwe_stolen / sonnet: claims_supported
- e1_letin / haiku: claims_supported
- e1_letin / nova-lite: claims_supported, mentions
- e1_whose / sonnet: claims_supported
- e2_holder / nova-lite: claims_supported, hedges
- e2_first / haiku: no_forbidden_claim
- e3_allowed / sonnet: claims_supported
- e3_who / haiku: claims_supported
- e6_escalate / haiku: claims_supported, mentions
- e6_escalate / sonnet: mentions
- e6_escalate / nova-lite: mentions
- e6_doing / haiku: claims_supported
