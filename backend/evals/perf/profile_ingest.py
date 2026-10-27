# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Measure the twin's per-frame cost on real WiseNET detections (no video decode / inference).

Replays cached YOLO detections of one set through ingest -> rules -> prune, exactly as live mode
would, into a file-backed SQLite DB, and reports per-stage latency. From backend/:
  .\.venv\Scripts\python.exe -m evals.perf.profile_ingest [--set 5] [--cprofile]
"""
import argparse, cProfile, json, os, pstats, statistics, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shapely.geometry import Polygon  # noqa: E402

from src.core.timeutil import canonical_utc  # noqa: E402
from src.rules.engine import IncidentRuleEngine  # noqa: E402
from src.rules.policy import load_access_policy  # noqa: E402
from src.sites import wisenet as W  # noqa: E402
from src.twin.bootstrap import TwinBootstrapService  # noqa: E402


def run(set_n):
    db = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "p.db")
    eng = TwinBootstrapService.bootstrap(db_path=db, seed_path=str(W.SITE / "floorplan_seed.yaml"))
    rules = IncidentRuleEngine(eng.store, eng.state, load_access_policy(str(W.SITE / "access_policy.yaml")))
    dets = json.loads((W.SITE / "runs" / f"set_{set_n}_yolo11s_detections.json").read_text())
    info, cal = W.set_info(set_n), W.load_calibration()
    room_pt = {z: Polygon([(p.x, p.y) for p in vs]).representative_point().coords[0]
               for z, vs in eng.store.get_all_vertices_by_zone().items()}
    frames = {}
    for cam, lst in dets.items():
        for d in lst:
            frames.setdefault((d["t"], cam), []).append(d)
    stages = {"ingest": [], "rules": [], "prune": [], "frame_total": []}
    n_obs = n_events = 0
    for (t, cam), ds in sorted(frames.items()):
        ts = canonical_utc(t)
        f0 = time.perf_counter()
        a = time.perf_counter(); evs = []
        for d in ds:
            x, y, _ = W.floor_point(cam, d["box"], (info["w"], info["h"]), cal, room_pt)
            evs += eng.ingest_observation(f"{cam}:{d['id']}", cam, x, y, ts, d["conf"])
        stages["ingest"].append(time.perf_counter() - a)
        a = time.perf_counter(); rules.evaluate(evs); stages["rules"].append(time.perf_counter() - a)
        a = time.perf_counter(); lost = eng.prune_tracks(now_iso=ts, ttl_seconds=2.0); rules.evaluate(lost)
        stages["prune"].append(time.perf_counter() - a)
        stages["frame_total"].append(time.perf_counter() - f0)
        n_obs += len(ds); n_events += len(evs) + len(lost)
    print(f"set {set_n}: {len(frames)} camera-frames, {n_obs} detections, {n_events} twin events")
    for k, v in stages.items():
        v = sorted(v)
        print(f"  {k:12s} mean {1000*statistics.mean(v):6.2f} ms   p95 {1000*v[int(.95*len(v))]:6.2f} ms   max {1000*v[-1]:7.2f} ms")
    budget = 1000 / (6 * 5)
    print(f"  live budget for 6 cameras x 5 fps: {budget:.1f} ms per camera-frame")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--set", type=int, default=5); ap.add_argument("--cprofile", action="store_true")
    a = ap.parse_args()
    if a.cprofile:
        pr = cProfile.Profile(); pr.enable(); run(a.set); pr.disable()
        pstats.Stats(pr).sort_stats("cumulative").print_stats(22)
    else:
        run(a.set)
