"""WiseNET real-building validation: run all cameras of a recording set through the tracker,
feed the twin, and score its per-room headcount against the dataset's ground truth.

Data: WiseNET (CC BY-NC-SA 4.0) - internal, non-commercial validation only.
Heavy libraries (cv2, ultralytics) are imported inside functions.

Room double counting: two cameras cover the corridor (cam_1, cam_2) and two the workroom
(cam_4, cam_5). Without cross-camera re-identification each person would be counted once per
camera, so only one PRIMARY camera per room feeds the twin; the others are shown on the
video wall and used for cross-checks.
"""
from datetime import datetime, timedelta, timezone
import configparser
import json
from pathlib import Path
import statistics
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[3]
SITE = REPO / "data" / "sites" / "wisenet"
DATA = REPO / "data" / "datasets" / "wisenet" / "wisenet_dataset"
SPACE_TO_ZONE = {"space 1": "zone_corridor", "space 2": "zone_workroom", "space 3": "zone_vision_lab",
                 "space 4": "zone_print_room", "space 5": "zone_it_annex", "space 6": "zone_office_307"}
PRIMARY = {"zone_corridor": "cam_2", "zone_workroom": "cam_4", "zone_vision_lab": "cam_3", "zone_print_room": "cam_6"}


def set_info(set_n: int) -> Dict[str, Any]:
    cp = configparser.ConfigParser()
    cp.read(DATA / "manual_annotations" / "people_tracking" / f"set_{set_n}" / "setinfo.ini")
    s = cp["Set"]
    cams = [int(x) for x in s["sequences"].strip("{}").split(",")]
    return {"start": datetime.fromisoformat(s["initialTime"].replace("Z", "+00:00")), "fps": float(s["frameRate"]),
            "w": int(s["imWidth"]), "h": int(s["imHeight"]), "cams": cams}


def load_calibration() -> Dict[str, Any]:
    return json.loads((SITE / "calibration.json").read_text(encoding="utf-8"))


def detect_set(set_n: int, tracker_factory, process_fps: float = 5.0, cache: bool = True, model_tag: str = "yolo11s") -> Dict[str, List[dict]]:
    """Track people in every camera of a set. Returns {cam_id: [{t, id, box:[x,y,w,h], conf}]} (cached)."""
    import cv2

    out_path = SITE / "runs" / f"set_{set_n}_{model_tag}_detections.json"
    if cache and out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    info = set_info(set_n)
    stride = max(1, round(info["fps"] / process_fps))
    result: Dict[str, List[dict]] = {}
    for c in info["cams"]:
        tracker = tracker_factory()          # fresh tracker per camera: IDs are per camera
        cap = cv2.VideoCapture(str(DATA / "video_sets" / f"set_{set_n}" / f"video{set_n}_{c}.avi"))
        dets, idx = [], 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                t = (info["start"] + timedelta(seconds=idx / info["fps"])).isoformat()
                for d in tracker.update(frame):
                    b = d.bbox
                    dets.append({"t": t, "frame": idx, "id": d.local_track_id, "conf": round(d.confidence, 3),
                                 "box": [round(b.u_min, 1), round(b.v_min, 1), round(b.u_max - b.u_min, 1), round(b.v_max - b.v_min, 1)]})
            idx += 1
        cap.release()
        result[f"cam_{c}"] = dets
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def floor_point(cam: str, box: List[float], img_wh: Tuple[int, int], cal: Dict[str, Any], room_points: Dict[str, Tuple[float, float]]):
    """Feet -> floor (metres). Metric cameras use the homography (inside their trusted range);
    room-level cameras (and far-range metric sightings) return the room's representative point."""
    import cv2
    import numpy as np

    cc = cal["cameras"][cam]
    zone = {"s1": "zone_corridor", "s2": "zone_workroom", "s3": "zone_vision_lab", "s4": "zone_print_room"}[cc["space"]]
    x, y, w, h = box
    u, v = x + w / 2, y + h
    if cc["mode"] == "metric":
        sx, sy = cc["image_size"][0] / img_wh[0], cc["image_size"][1] / img_wh[1]
        u, v = u * sx, v * sy
        if v >= cc.get("trusted_min_foot_v", 0) and v < cc["image_size"][1] - 5:
            p = cv2.perspectiveTransform(np.float32([[[u, v]]]), np.array(cc["pixel_to_floor_H"]))[0, 0]
            return float(p[0]), float(p[1]), "metric"
    return room_points[zone][0], room_points[zone][1], "room"


def replay_into_twin(set_n: int, dets: Dict[str, List[dict]], engine, rule_engine=None, exit_seconds: float = 2.0) -> Dict[str, Any]:
    """Feed primary-camera sightings into the twin in time order, with event-time exits per camera."""
    from shapely.geometry import Polygon

    try:
        from src.core.timeutil import canonical_utc
    except ImportError:
        from core.timeutil import canonical_utc

    info, cal = set_info(set_n), load_calibration()
    verts = engine.store.get_all_vertices_by_zone()
    room_points = {z: Polygon([(p.x, p.y) for p in vs]).representative_point().coords[0] for z, vs in verts.items()}
    primary_cams = set(PRIMARY.values())
    rows = sorted(((d["t"], cam, d) for cam, lst in dets.items() if cam in primary_cams for d in lst), key=lambda r: (r[0], r[1]))
    stats = {"observations": 0, "metric": 0, "room": 0, "incidents": []}
    last_t = None
    for t, cam, d in rows:
        x, y, mode = floor_point(cam, d["box"], (info["w"], info["h"]), cal, room_points)
        evs = engine.ingest_observation(f"{cam}:set{set_n}:{d['id']}", cam, x, y, t, d["conf"])
        stats["observations"] += 1
        stats[mode] += 1
        if rule_engine is not None:
            stats["incidents"] += [i.incident_id for i in rule_engine.evaluate(evs)]
        if t != last_t:   # one exit sweep per timestamp: a person unseen for exit_seconds has left the view
            lost = engine.prune_tracks(now_iso=canonical_utc(t), ttl_seconds=exit_seconds, track_id_prefix=None)
            if lost and rule_engine is not None:
                stats["incidents"] += [i.incident_id for i in rule_engine.evaluate(lost)]
            last_t = t
    end = info["start"] + timedelta(seconds=max((d["frame"] for lst in dets.values() for d in lst), default=0) / info["fps"] + exit_seconds + 1)
    lost = engine.prune_tracks(now_iso=canonical_utc(end), ttl_seconds=exit_seconds)
    if lost and rule_engine is not None:
        stats["incidents"] += [i.incident_id for i in rule_engine.evaluate(lost)]
    stats["incidents"] = list(dict.fromkeys(stats["incidents"]))
    return stats


def _gt_time(text: str) -> datetime:
    # The dataset has typos like "2017-04-24-T12:15:00.001Z" (extra dash before T).
    return datetime.fromisoformat(text.strip().replace("-T", "T").replace("Z", "+00:00"))


def gt_occupancy(set_n: int) -> Tuple[Dict[str, List[Tuple[datetime, datetime]]], datetime, datetime]:
    gt = json.loads((DATA / "manual_annotations" / "people_tracking" / f"set_{set_n}" / "gt.json").read_text())
    per_zone: Dict[str, List[Tuple[datetime, datetime]]] = {}
    lo, hi = None, None
    for tr in gt["tracks"]:
        for tl in tr["tracklets"]:
            a, b = _gt_time(tl["start"]), _gt_time(tl["end"])
            per_zone.setdefault(SPACE_TO_ZONE.get(tl["location"].strip().lower(), tl["location"]), []).append((a, b))
            lo = a if lo is None or a < lo else lo
            hi = b if hi is None or b > hi else hi
    return per_zone, lo, hi


def score_set(set_n: int, engine) -> Dict[str, Any]:
    """Per-room headcount, system vs ground truth, sampled every second, for rooms with a primary camera."""
    try:
        from src.twin.history import occupancy_series
    except ImportError:
        from twin.history import occupancy_series

    per_zone, lo, hi = gt_occupancy(set_n)
    cams = {f"cam_{c}" for c in set_info(set_n)["cams"]}
    zones = [z for z, cam in PRIMARY.items() if cam in cams]
    events = engine.store.get_events_ascending(event_types=["ZONE_ENTERED", "ZONE_EXITED", "CROSS_ZONE_TRANSITION", "TRACK_LOST"])
    series = occupancy_series(events, lo, hi, 1)
    out = {}
    for z in zones:
        sys_counts = [b["occupancy"].get(z, 0) for b in series]
        gt_counts = []
        for b in series:
            t = datetime.fromisoformat(b["bucket_start"])
            gt_counts.append(sum(1 for a, e in per_zone.get(z, []) if a <= t < e))
        diffs = [s - g for s, g in zip(sys_counts, gt_counts)]
        out[z] = {"seconds": len(diffs), "exact_pct": round(100 * sum(d == 0 for d in diffs) / max(1, len(diffs)), 1),
                  "within1_pct": round(100 * sum(abs(d) <= 1 for d in diffs) / max(1, len(diffs)), 1),
                  "mae": round(statistics.mean(abs(d) for d in diffs), 2) if diffs else None,
                  "bias": round(statistics.mean(diffs), 2) if diffs else None,
                  "gt_peak": max(gt_counts, default=0), "sys_peak": max(sys_counts, default=0)}
    return out
