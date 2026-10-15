"""Badge rules on REAL movement: WiseNET footage + SIMULATED badge swipes at the vision-lab door (d4).

The people, their timing and the camera sightings are real; only the badge events are simulated,
timed from the dataset's ground-truth entry times. From backend/:
  .\\.venv\\Scripts\\python.exe -m evals.site.badge_demo [--investigate]
Internal use only (WiseNET is CC BY-NC-SA 4.0).
"""
import argparse, json, os, sys, tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shapely.geometry import Polygon  # noqa: E402

from src.access.config import load_access_config  # noqa: E402
from src.access.service import AccessControlService  # noqa: E402
from src.core.timeutil import canonical_utc  # noqa: E402
from src.live.entry_gate import EntryGate  # noqa: E402
from src.rules.correlation import CorrelationEngine  # noqa: E402
from src.rules.engine import IncidentRuleEngine  # noqa: E402
from src.rules.policy import load_access_policy  # noqa: E402
from src.sites import wisenet as W  # noqa: E402
from src.twin.bootstrap import TwinBootstrapService  # noqa: E402

# set -> list of (seconds after the first real entry into the vision lab, badge, result)
PLANS = {
    1: {"story": "One person enters; a valid badge swipe 2 s before.", "swipes": [(-2.0, "badge_1003", "GRANTED")]},
    2: {"story": "One person enters with NO badge swipe.", "swipes": []},
    3: {"story": "Two people enter 1.4 s apart on ONE badge swipe.", "swipes": [(-2.0, "badge_1003", "GRANTED")]},
    5: {"story": "Fourteen people stream in within 14 s on ONE badge swipe.", "swipes": [(-2.0, "badge_1003", "GRANTED")]},
}


class _NoTopology:  # tracks are per camera here, so cross-camera transit checks do not apply
    def min_transit_seconds(self, a, b):
        return 0.0


def first_lab_entry(set_n):
    gt = json.loads((W.DATA / "manual_annotations" / "people_tracking" / f"set_{set_n}" / "gt.json").read_text())
    times = [W._gt_time(b["start"]) for tr in gt["tracks"] for a, b in zip(tr["tracklets"], tr["tracklets"][1:])
             if b["location"].strip().lower() == "space 3"]
    return min(times)


def run(set_n, investigate=False):
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "b.db")
    eng = TwinBootstrapService.bootstrap(db_path=os.environ["DATABASE_URL"], seed_path=str(W.SITE / "floorplan_seed.yaml"))
    policy = load_access_policy(str(W.SITE / "access_policy.yaml"))
    access = load_access_config(str(W.SITE / "doors.yaml"))
    dets = json.loads((W.SITE / "runs" / f"set_{set_n}_yolo11s_detections.json").read_text())
    # A real door entry starts AT the door and is tracked continuously (src/live/entry_gate.py).
    # The gate only uses what was seen up to each decision, exactly as in live operation.
    # The dataset has one door region per recording resolution (camera 3 was also mounted differently).
    res = f"{W.set_info(set_n)['w']}_{W.set_info(set_n)['h']}"
    roi = json.loads((W.DATA / "network_enviroment" / "camera_calibration" / res / "cam_3.json").read_text())["regionsOfInterest"][0]["xywh"]
    gate = EntryGate({"cam_3": {"roi": roi, "image_size": [W.set_info(set_n)["w"], W.set_info(set_n)["h"]],
                                "margin_frac": 0.33, "min_sightings": 5}})

    rules = IncidentRuleEngine(eng.store, eng.state, policy,
                               correlation=CorrelationEngine(policy=policy, access_config=access, topology=_NoTopology(),
                                                             store=eng.store,
                                                             entry_filter=gate.entry_filter, entry_confirm=gate.entry_confirm))
    badges = AccessControlService(eng.store, access, rule_engine=rules)
    info, cal = W.set_info(set_n), W.load_calibration()
    verts = eng.store.get_all_vertices_by_zone()
    room_pt = {z: Polygon([(p.x, p.y) for p in vs]).representative_point().coords[0] for z, vs in verts.items()}
    t0 = first_lab_entry(set_n)
    timeline = [(canonical_utc(t0 + timedelta(seconds=dt)), "badge", (b, r)) for dt, b, r in PLANS[set_n]["swipes"]]
    timeline += [(canonical_utc(d["t"]), "obs", (cam, d)) for cam, lst in dets.items() if cam in W.PRIMARY.values() for d in lst]
    timeline.sort(key=lambda r: (r[0], r[1] != "badge"))
    incidents, last = [], None
    for t, kind, payload in timeline:
        if kind == "badge":
            _, inc = badges.ingest("door_d4", payload[0], payload[1], t)
            incidents += inc
            continue
        cam, d = payload
        x, y, _ = W.floor_point(cam, d["box"], (info["w"], info["h"]), cal, room_pt)
        gate.observe(cam, f"{cam}:set{set_n}:{d['id']}", d["box"], (info["w"], info["h"]))
        incidents += rules.evaluate(eng.ingest_observation(f"{cam}:set{set_n}:{d['id']}", cam, x, y, t, d["conf"]))
        if t != last:
            lost = eng.prune_tracks(now_iso=t, ttl_seconds=2.0)
            if lost:
                incidents += rules.evaluate(lost)
            last = t
    final = {}
    for i in incidents:
        final[i.incident_id] = eng.store.get_incident(i.incident_id)
    summary = {}
    for i in final.values():
        summary[i.incident_type] = summary.get(i.incident_type, 0) + 1
    print(f"\nset {set_n}: {PLANS[set_n]['story']}\n  real first entry into the lab: {t0.strftime('%H:%M:%S.%f')[:-3]}  ->  incidents: {summary or 'none'}")
    for i in sorted(final.values(), key=lambda i: i.start_time):
        if i.incident_type in ("TAILGATING", "ACCESS_WITHOUT_BADGE", "BADGE_WITHOUT_ENTRY", "ZONE_OVER_CAPACITY"):
            print(f"   {i.start_time[11:23]} {i.incident_type:22s} {i.summary[:150]}")
    if investigate:
        from src.config import get_settings
        from src.runtime import make_investigation_service
        get_settings.cache_clear()
        svc = make_investigation_service(get_settings(), eng.store, policy, access)
        target = next((i for i in final.values() if i.incident_type in ("TAILGATING", "ACCESS_WITHOUT_BADGE")), None)
        if target:
            out = svc.investigate(target.incident_id, "What happened at the vision lab door, and how sure can we be?")
            r, v = out["result"] or {}, out["verification"]
            print(f"  AI INVESTIGATION of {target.incident_type}: status {out['status']}, verified {v.get('passed')}, "
                  f"claims unsupported {v.get('claims', {}).get('unsupported')}, cost ${out['cost_usd']:.4f}")
            print("   ANSWER:", r.get("answer"))
            print("   WHO:", r.get("person_identity"))
            json.dump(out, open(W.SITE / "runs" / f"set_{set_n}_investigation.json", "w"), indent=1)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--investigate", action="store_true"); a = ap.parse_args()
    for n in PLANS:
        run(n, investigate=a.investigate and n == 3)
