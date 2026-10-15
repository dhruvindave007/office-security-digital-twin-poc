"""Run the investigation eval set against one or more Bedrock models.

From backend/:
  .\\.venv\\Scripts\\python.exe -m evals.investigation.run_eval --models haiku,sonnet,nova-lite --budget 2.0
  .\\.venv\\Scripts\\python.exe -m evals.investigation.run_eval --models haiku --cases tg_real,e2_holder

Every case runs in a fresh twin (scenarios never mix). Claims of EVERY model are judged by the same
fixed judge (Claude Haiku 5.5) so the comparison is fair. Writes evals/investigation/results/<ts>/:
  results.json  (everything), summary.md (tables), review.csv (one row per answer for human review).
Spends real money: --budget stops the run before the estimated spend exceeds it.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time

import yaml

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))

from src.config import get_settings  # noqa: E402
from src.runtime import build_runtime  # noqa: E402
from src.simulator.scenarios import load_scenario  # noqa: E402

MODELS = {
    "haiku": "global.anthropic.claude-haiku-5-5",
    "sonnet": "global.anthropic.claude-sonnet-5-5",
    "nova-lite": "us.amazon.nova-lite-v1:0",
    "nova-pro": "us.amazon.nova-pro-v1:0",
    "nova2-lite": "global.amazon.nova-2-lite-v1:0",
}
JUDGE_MODEL = MODELS["haiku"]
HEDGES = ("not enough evidence", "cannot", "can't", "unknown", "no evidence", "not determine", "unclear",
          "does not show", "do not show", "doesn't show", "not recorded", "not identified", "unidentified",
          "insufficient", "not possible to", "no record", "not confirm", "unable to", "not established",
          "does not indicate", "do not indicate", "not verified", "probably", "likely", "possibly",
          "may have", "might", "inference", "should be confirmed", "needs to be verified")
UNKNOWN_IDENTITY = ("not identified", "unidentified", "unknown", "not enough evidence", "no person_id",
                    "cannot be identified", "not known", "no identity")


UNCERTAIN_RE = re.compile(
    r"\b(not|no|cannot|can't|unable to|without)\b[^.]{0,40}?\b(confirm|identif|determin|show|establish|know|"
    r"enough|evidence|verif|clear|record|link|prove|indicate|tell)", re.I)


def admits_uncertainty(text):
    return any(h in text for h in HEDGES) or bool(UNCERTAIN_RE.search(text))


def find_scenario(name, dirs):
    for d in dirs:
        p = (BACKEND / d / f"{name}.yaml").resolve()
        if p.exists():
            return p
    raise FileNotFoundError(f"scenario {name} not found in {dirs}")


def fresh_runtime():
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "eval.db")
    get_settings.cache_clear()
    return build_runtime(get_settings())


def score_case(case, out):
    """Automatic checks. Each returns True/False; None = not applicable."""
    exp = case.get("expect", {})
    res = out.get("result") or {}
    ver = out.get("verification") or {}
    flat = json.dumps(res).lower()
    answer = str(res.get("answer") or "").lower()
    claims_text = " ".join([answer, str(res.get("summary") or "").lower(),
                            " ".join(str(f.get("statement", "")).lower() for f in res.get("findings") or [])])
    identity = str(res.get("person_identity") or "").lower()
    checks = {
        "valid_json": out.get("status") == "COMPLETED",
        "ids_exist": ver.get("ids_passed", ver.get("passed")) if res else False,
        "claims_supported": (ver.get("claims") or {}).get("unsupported", 1) == 0 if res else False,
        "neutral_wording": not ver.get("banned_words") if res else False,
        "mentions": all(m.lower() in flat for m in exp["mention"]) if exp.get("mention") else None,
        "hedges": (admits_uncertainty(answer) == exp["hedge"]) if "hedge" in exp else None,
        "identity": None,
        "number": (re.search(rf"(?<![\d.]){exp['number']}(?![\d.])", answer + " " + flat) is not None) if "number" in exp else None,
        "no_forbidden_claim": (not any(re.search(rx, claims_text) for rx in exp["forbid"])) if exp.get("forbid") else None,
    }
    if exp.get("identity") == "unknown":
        checks["identity"] = any(w in identity for w in UNKNOWN_IDENTITY)
    elif exp.get("identity") == "label":
        checks["identity"] = "label" in identity and bool(re.search(r"person_[a-z_0-9]+", identity))
    applicable = {k: v for k, v in checks.items() if v is not None}
    return checks, sum(applicable.values()) / len(applicable)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="haiku")
    ap.add_argument("--cases", default="all")
    ap.add_argument("--budget", type=float, default=2.0, help="stop before estimated spend exceeds this (USD)")
    args = ap.parse_args()

    spec = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    cases = spec["cases"] if args.cases == "all" else [c for c in spec["cases"] if c["id"] in args.cases.split(",")]
    models = [m.strip() for m in args.models.split(",")]
    out_dir = HERE / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, spent = [], 0.0
    for case in cases:
        for m in models:
            if spent >= args.budget:
                print(f"BUDGET REACHED (${spent:.4f} >= ${args.budget}); stopping."); break
            rt = fresh_runtime()
            scenario = load_scenario(find_scenario(case["scenario"], spec["scenario_dirs"]))
            run = rt.scenario_runner.run(scenario)
            store = rt.twin_engine.store
            incs = [store.get_incident(i) for i in run.incidents]
            target = next((i for i in incs if i.incident_type == case["incident"]
                           and (not case.get("track") or i.track_id == case["track"])), None)
            if target is None:
                rows.append({"case": case["id"], "model": m, "error": f"no {case['incident']} incident raised"}); continue

            svc = rt.investigation_service
            svc.model_id = MODELS[m]
            svc.daily_budget_usd = 1e9  # the eval enforces its own --budget
            svc.claim_checker.model_id = JUDGE_MODEL  # one fixed judge for every model
            drop = set(case.get("drop_event_types") or [])
            if drop:
                base_build = svc.evidence_builder.build

                def build(incident_id, _b=base_build, _d=drop, _strip=bool(case.get("strip_rule_summaries"))):
                    ev = _b(incident_id)
                    ev["events"] = [e for e in ev["events"] if e["type"] not in _d]
                    if _strip:  # rule summaries can restate the dropped facts
                        for inc in [ev["incident"], *ev["related_incidents"]]:
                            inc["rule_summary"] = ""
                    return ev
                svc.evidence_builder.build = build
            t = time.perf_counter()
            try:
                out = svc.investigate(target.incident_id, case["question"])
            except Exception as e:  # record and continue: one failure must not stop the eval
                rows.append({"case": case["id"], "model": m, "error": f"{type(e).__name__}: {e}"}); continue
            checks, score = score_case(case, out)
            spent += out["cost_usd"] or 0.0
            rows.append({"case": case["id"], "category": case.get("category"), "model": m, "question": case["question"],
                         "score": round(score, 3), "checks": checks, "cost_usd": out["cost_usd"],
                         "seconds": round(time.perf_counter() - t, 1), "verification": out["verification"],
                         "result": out["result"]})
            print(f"{case['id']:16s} {m:10s} score={score:.2f} ${out['cost_usd']:.4f} "
                  f"failed={[k for k, v in checks.items() if v is False]}")

    (out_dir / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    with open(out_dir / "review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case", "model", "question", "answer", "person_identity", "auto_score", "auto_failed_checks",
                    "unsupported_claims", "HUMAN_correct(yes/no/partly)", "HUMAN_notes"])
        for r in rows:
            res = r.get("result") or {}
            w.writerow([r["case"], r["model"], r.get("question", ""), res.get("answer", r.get("error", "")),
                        res.get("person_identity", ""), r.get("score", 0),
                        ";".join(k for k, v in (r.get("checks") or {}).items() if v is False),
                        "; ".join(c["text"] for c in ((r.get("verification") or {}).get("claims") or {}).get("claims", [])
                                  if c["verdict"] == "unsupported"), "", ""])

    lines = [f"# Investigation eval {out_dir.name}", "", f"Cases: {len(cases)} · models: {', '.join(models)} · "
             f"judge: {JUDGE_MODEL} · spent: ${spent:.4f}", "",
             "| Model | Mean score | All checks passed | Unsupported claims | Errors | Mean cost | Mean seconds |",
             "|---|---|---|---|---|---|---|"]
    for m in models:
        ok = [r for r in rows if r["model"] == m and "error" not in r]
        err = sum(1 for r in rows if r["model"] == m and "error" in r)
        if not ok:
            lines.append(f"| {m} | – | – | – | {err} | – | – |"); continue
        uns = sum((r["verification"].get("claims") or {}).get("unsupported", 0) for r in ok)
        lines.append(f"| {m} | {statistics.mean(r['score'] for r in ok):.3f} | {sum(r['score'] == 1 for r in ok)}/{len(ok)} | "
                     f"{uns} | {err} | ${statistics.mean(r['cost_usd'] for r in ok):.4f} | {statistics.mean(r['seconds'] for r in ok):.1f} |")
    lines += ["", "## Failed checks by case", ""]
    for r in rows:
        failed = [k for k, v in (r.get("checks") or {}).items() if v is False]
        if failed or "error" in r:
            lines.append(f"- {r['case']} / {r['model']}: {r.get('error') or ', '.join(failed)}")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6 + len(models)]))
    print(f"\nwritten: {out_dir}")


if __name__ == "__main__":
    main()
