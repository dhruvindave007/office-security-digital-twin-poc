"""Validate the twin on the real WiseNET building (all recording sets) against its ground truth.

From backend/:  .\.venv\Scripts\python.exe -m evals.site.run_wisenet [--sets 1,2,...]
Writes data/sites/wisenet/runs/validation.json and validation.md. Internal use only (CC BY-NC-SA 4.0).
"""
import argparse, json, os, statistics, sys, tempfile, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import get_settings  # noqa: E402
from src.runtime import make_tracker  # noqa: E402
from src.sites import wisenet as W  # noqa: E402
from src.twin.bootstrap import TwinBootstrapService  # noqa: E402
from src.vision.eval.mot import evaluate_sequence  # noqa: E402

MODEL = "yolo11s_openvino_model"


def detection_quality(set_n, dets, cams):
    out = {}
    for c in cams:
        man = json.loads((W.DATA / "manual_annotations" / "people_detection" / f"set_{set_n}" / f"video{set_n}_{c}.json").read_text())
        frames = {d["frame"] for d in dets[f"cam_{c}"]} | {fr["frameNumber"] for fr in man["frames"]}
        stride = 6 if W.set_info(set_n)["fps"] >= 29 else 5
        gt = {fr["frameNumber"]: [(d["id"], (d["xywh"][0], d["xywh"][1], d["xywh"][0] + d["xywh"][2], d["xywh"][1] + d["xywh"][3]))
                                  for d in fr["detections"]] for fr in man["frames"] if fr["frameNumber"] % stride == 0}
        pred = {}
        for d in dets[f"cam_{c}"]:
            x, y, w, h = d["box"]; pred.setdefault(d["frame"], []).append((d["id"], (x, y, x + w, y + h)))
        out[f"cam_{c}"] = evaluate_sequence(gt, pred) if gt else None
    return out


def fused_counts(set_n, dets):
    """Room headcount = max over the cameras covering the room (no double counting without re-ID)."""
    per_zone, lo, hi = W.gt_occupancy(set_n)
    cover = {"zone_corridor": ["cam_1", "cam_2"], "zone_workroom": ["cam_4", "cam_5"], "zone_vision_lab": ["cam_3"], "zone_print_room": ["cam_6"]}
    secs = range(int(lo.timestamp()), int(hi.timestamp()))
    bysec = {}
    for cam, lst in dets.items():
        for d in lst:
            bysec.setdefault((cam, int(datetime.fromisoformat(d["t"]).timestamp())), set()).add(d["id"])
    res = {}
    for z, cams in cover.items():
        cams = [c for c in cams if c in dets]
        if not cams: continue
        diffs = [max(len(bysec.get((c, t), ())) for c in cams) - sum(1 for a, e in per_zone.get(z, []) if a.timestamp() <= t < e.timestamp()) for t in secs]
        res[z] = {"seconds": len(diffs), "exact_pct": round(100 * sum(d == 0 for d in diffs) / len(diffs), 1),
                  "within1_pct": round(100 * sum(abs(d) <= 1 for d in diffs) / len(diffs), 1), "bias": round(statistics.mean(diffs), 2)}
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sets", default=",".join(str(i) for i in range(1, 12))); a = ap.parse_args()
    s = get_settings(); results = {}
    for n in [int(x) for x in a.sets.split(",")]:
        t0 = time.perf_counter()
        dets = W.detect_set(n, lambda: make_tracker(s, model=MODEL, device="intel:gpu"))
        eng = TwinBootstrapService.bootstrap(db_path="sqlite:///" + os.path.join(tempfile.mkdtemp(), "w.db"), seed_path=str(W.SITE / "floorplan_seed.yaml"))
        rep = W.replay_into_twin(n, dets, eng)
        results[n] = {"twin_primary_camera": W.score_set(n, eng), "fused_max_over_cameras": fused_counts(n, dets),
                      "detection": detection_quality(n, dets, W.set_info(n)["cams"]),
                      "observations": rep["observations"], "metric": rep["metric"], "room": rep["room"], "seconds": round(time.perf_counter() - t0)}
        print(f"set {n} done in {results[n]['seconds']}s", flush=True)
    out = W.SITE / "runs"; out.mkdir(parents=True, exist_ok=True)
    (out / "validation.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    # aggregate
    lines = ["# WiseNET validation (real building, ground truth)", "", f"Model: {MODEL} on Intel GPU (OpenVINO). Sets: {', '.join(map(str, results))}. Internal use only (CC BY-NC-SA 4.0).", "",
             "## Room headcount accuracy (per second, time-weighted across sets)", "", "| Room | Method | Seconds | Exact | Within ±1 | Bias (people) |", "|---|---|---|---|---|---|"]
    for method in ("twin_primary_camera", "fused_max_over_cameras"):
        agg = {}
        for r in results.values():
            for z, v in r[method].items():
                g = agg.setdefault(z, [0, 0, 0, 0.0]); g[0] += v["seconds"]; g[1] += v["exact_pct"] * v["seconds"] / 100; g[2] += v["within1_pct"] * v["seconds"] / 100; g[3] += v["bias"] * v["seconds"]
        for z, (sec, ex, w1, b) in agg.items():
            lines.append(f"| {z} | {method} | {sec} | {100*ex/sec:.1f}% | {100*w1/sec:.1f}% | {b/sec:+.2f} |")
    lines += ["", "## Person detection vs hand-drawn boxes (all sets)", "", "| Camera | GT boxes | Precision | Recall |", "|---|---|---|---|"]
    det = {}
    for r in results.values():
        for c, m in r["detection"].items():
            if m: g = det.setdefault(c, [0, 0, 0]); g[0] += m["gt_boxes"]; g[1] += m["pred_boxes"]; g[2] += m["true_positives"]
    for c, (gtb, pb, tp) in sorted(det.items()):
        lines.append(f"| {c} | {gtb} | {tp/max(1,pb):.2f} | {tp/max(1,gtb):.2f} |")
    (out / "validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
