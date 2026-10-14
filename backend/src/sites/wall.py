"""Synchronised multi-camera video wall + live floor plan for a WiseNET recording set.

Top: every camera of the set in a grid (boxes + track numbers). Bottom: the floor plan with each
person's position (metric cameras: exact dot; room-level cameras: dot at the room's centre) and,
per room, the system's headcount next to the ground-truth headcount.
Output: data/sites/wisenet/runs/wall_set_<n>.mp4 (H.264). Internal use only (CC BY-NC-SA 4.0).
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Dict, List

from src.sites import wisenet as W

TILE_W, TILE_H, PLAN_H = 640, 360, 400
COLORS = [(66, 135, 245), (12, 83, 201), (82, 196, 103), (18, 156, 226), (180, 90, 200), (60, 60, 230), (200, 170, 40)]
ROOM_CAMS = {"zone_corridor": ["cam_1", "cam_2"], "zone_workroom": ["cam_4", "cam_5"], "zone_vision_lab": ["cam_3"], "zone_print_room": ["cam_6"]}


def render_wall(set_n: int, dets: Dict[str, List[dict]], out_path: Path, max_seconds: float = 0) -> Dict:
    import cv2
    import numpy as np
    import yaml
    from shapely.geometry import Polygon

    info, cal = W.set_info(set_n), W.load_calibration()
    seed = yaml.safe_load((W.SITE / "floorplan_seed.yaml").read_text(encoding="utf-8"))
    zones = {z["zone_id"]: [(v["x"], v["y"]) for v in z["vertices"]] for z in seed["zones"]}
    names = {z["zone_id"]: z["name"] for z in seed["zones"]}
    room_pt = {z: Polygon(p).representative_point().coords[0] for z, p in zones.items()}
    allp = np.array([p for ps in zones.values() for p in ps])
    x0, y0 = allp.min(0) - 0.5; x1, y1 = allp.max(0) + 0.5
    ppm = min((3 * TILE_W - 360) / (x1 - x0), (PLAN_H - 20) / (y1 - y0))
    P = lambda x, y: (int(20 + (x - x0) * ppm), int(10 + (y1 - y) * ppm))           # north up
    per_zone, _, _ = W.gt_occupancy(set_n)
    cams = [f"cam_{c}" for c in info["cams"]]
    caps = {c: cv2.VideoCapture(str(W.DATA / "video_sets" / f"set_{set_n}" / f"video{set_n}_{c[4:]}.avi")) for c in cams}
    by_frame = {c: {} for c in cams}
    for c in cams:
        for d in dets.get(c, []):
            by_frame[c].setdefault(d["frame"], []).append(d)
    stride = max(1, round(info["fps"] / 5))
    W_out, H_out = 3 * TILE_W, 2 * TILE_H + PLAN_H
    raw_path = out_path.with_suffix(".raw.avi")  # OpenCV here lacks an H.264 encoder: write MJPG, convert with ffmpeg
    writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"MJPG"), info["fps"] / stride, (W_out, H_out))
    last: Dict[str, List[dict]] = {c: [] for c in cams}
    idx, frames = 0, 0
    total = min(int(caps[c].get(cv2.CAP_PROP_FRAME_COUNT)) for c in cams)
    if max_seconds:
        total = min(total, int(max_seconds * info["fps"]))
    while idx < total:
        imgs = {c: caps[c].read()[1] for c in cams}
        if idx % stride:
            idx += 1
            continue
        canvas = np.full((H_out, W_out, 3), 24, np.uint8)
        t = info["start"] + timedelta(seconds=idx / info["fps"])
        floor_dots = []
        for i, c in enumerate(cams):
            img = imgs[c]
            if img is None:
                continue
            ds = by_frame[c].get(idx, [])
            if ds:
                last[c] = ds
            sx, sy = TILE_W / img.shape[1], TILE_H / img.shape[0]
            tile = cv2.resize(img, (TILE_W, TILE_H))
            for d in ds:
                x, y, w, h = d["box"]; col = COLORS[d["id"] % len(COLORS)]
                cv2.rectangle(tile, (int(x * sx), int(y * sy)), (int((x + w) * sx), int((y + h) * sy)), col, 2)
                cv2.putText(tile, f"#{d['id']}", (int(x * sx) + 3, int(y * sy) + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
                fx, fy, mode = W.floor_point(c, d["box"], (img.shape[1], img.shape[0]), cal, room_pt)
                floor_dots.append((fx, fy, mode, c))
            cv2.rectangle(tile, (0, 0), (TILE_W, 24), (20, 20, 20), -1)
            cv2.putText(tile, f"{c}  {cal['cameras'][c]['space']}  {len(ds)} in view", (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            r, k = divmod(i, 3)
            canvas[r * TILE_H:(r + 1) * TILE_H, k * TILE_W:(k + 1) * TILE_W] = tile
        # floor plan
        plan = canvas[2 * TILE_H:, :]
        plan[:] = (246, 246, 244)
        for z, pts in zones.items():
            poly = np.array([P(x, y) for x, y in pts], np.int32)
            cv2.polylines(plan, [poly], True, (90, 70, 50), 2)
            cx, cy = P(*room_pt[z])
            cv2.putText(plan, names[z].split(" (")[0], (cx - 40, cy - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (70, 60, 50), 1)
        for fx, fy, mode, c in floor_dots:
            if mode == "metric":
                cv2.circle(plan, P(fx, fy), 6, (220, 110, 30), -1)   # BGR: blue
        # per-room counts: system (max over that room's cameras) vs ground truth
        panel_x = W_out - 330
        cv2.putText(plan, t.strftime("%H:%M:%S") + f"  set {set_n}", (panel_x, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 2)
        cv2.putText(plan, "room          system  truth", (panel_x, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 1)
        row = 0
        for z, rc in ROOM_CAMS.items():
            rc = [c for c in rc if c in cams]
            if not rc:
                continue
            sys_n = max(len(by_frame[c].get(idx, last[c])) for c in rc)
            gt_n = sum(1 for a, e in per_zone.get(z, []) if a <= t < e)
            col = (40, 140, 60) if sys_n == gt_n else (40, 120, 230) if abs(sys_n - gt_n) == 1 else (40, 40, 210)
            y = 92 + row * 30
            cv2.putText(plan, names[z].split(" (")[0][:13], (panel_x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1)
            cv2.putText(plan, f"{sys_n:>3}     {gt_n:>3}", (panel_x + 140, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            cx, cy = P(*room_pt[z])
            cv2.circle(plan, (cx, cy + 8), 15, col, -1)
            cv2.putText(plan, str(sys_n), (cx - 6, cy + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
            row += 1
        cv2.putText(plan, "blue dots: exact positions (calibrated corridor cameras); circles: room headcount", (20, PLAN_H - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 90), 1)
        writer.write(canvas)
        frames += 1
        idx += 1
    writer.release()
    for cap in caps.values():
        cap.release()
    import shutil
    import subprocess
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_path), "-vf", "scale=1600:-2", "-c:v", "libx264",
                        "-preset", "slow", "-crf", "27", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)], check=True)
        raw_path.unlink()
    else:
        out_path = raw_path
    return {"video": str(out_path), "frames": frames, "size": [W_out, H_out], "cameras": cams}
