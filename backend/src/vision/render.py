# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Render what the tracker sees: annotated CCTV video plus smooth (blurred) heatmaps.

Outputs (in data/clips/out/<clip>/):
  annotated.mp4      boxes, track numbers, floor zone and live count drawn on every processed frame
  heatmap_floor.png  smooth density of floor positions over the office plan (metres)
  heatmap_camera.png smooth density of foot points glowing over a frame of the camera view
cv2/numpy are imported inside the function (heavy libraries stay off the server import path).
"""
from pathlib import Path
from typing import Any, Dict, List, Tuple


def render_clip(config, tracker, homography, zone_resolver, zones: List[Dict[str, Any]],
                clips_dir: Path, out_dir: Path, max_frames: int = 0) -> Dict[str, Any]:
    import cv2
    import numpy as np

    try:
        from src.vision.geometry import PixelPoint
    except ImportError:
        from vision.geometry import PixelPoint

    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(clips_dir / config.video_file))
    raw_fps = cap.get(cv2.CAP_PROP_FPS)
    src_fps = config.source_fps_override or (raw_fps if 0 < raw_fps <= 120 else 25.0)
    stride = max(1, round(src_fps / config.process_fps))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = 1280 / W if W > 1280 else 1.0                     # keep the file small
    ow, oh = int(W * scale), int(H * scale)
    out_fps = src_fps / stride
    path = out_dir / "annotated.mp4"
    writer = None
    for fourcc in ("avc1", "H264", "mp4v"):                   # H.264 plays in browsers; mp4v is the fallback
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), out_fps, (ow, oh))
        if writer.isOpened():
            codec = fourcc
            break
    palette = [(66, 135, 245), (12, 83, 201), (82, 196, 103), (18, 156, 226), (180, 90, 200), (60, 60, 230)]
    feet: List[Tuple[float, float]] = []
    floor: List[Tuple[float, float]] = []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    backdrop_idx = total // 2  # a mid-clip frame: clips often open on a black fade-in
    tracks, idx, frames, first_frame = set(), 0, 0, None
    tracker.reset()
    while True:
        ok, frame = cap.read()
        if not ok or (max_frames and idx >= max_frames):
            break
        if idx % stride:
            idx += 1
            continue
        if first_frame is None or idx <= backdrop_idx:
            first_frame = frame.copy()  # clean (unannotated) backdrop for the camera heatmap
        dets = tracker.update(frame)
        for d in dets:
            b = d.bbox
            u, v = (b.u_min + b.u_max) / 2, b.v_max
            fu, fv = u * 1920 / W, v * 1080 / H                   # camera calibration is 1920x1080
            proj = homography.project_pixel_to_floor(config.camera_id, PixelPoint(u=fu, v=fv))
            zone = zone_resolver.resolve_zone(proj.floor_point, proj.geometry_version) or "outside zones"
            feet.append((u, v)); floor.append((proj.floor_point.x, proj.floor_point.y)); tracks.add(d.local_track_id)
            col = palette[d.local_track_id % len(palette)]
            cv2.rectangle(frame, (int(b.u_min), int(b.v_min)), (int(b.u_max), int(b.v_max)), col, 3)
            label = f"#{d.local_track_id} {zone.replace('zone_', '')}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            cv2.rectangle(frame, (int(b.u_min), int(b.v_min) - th - 10), (int(b.u_min) + tw + 8, int(b.v_min)), col, -1)
            cv2.putText(frame, label, (int(b.u_min) + 4, int(b.v_min) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.circle(frame, (int(u), int(v)), 6, col, -1)
        t = idx / src_fps
        hud = f"{config.camera_id}   t={t:5.1f}s   people in view: {len(dets)}   tracked so far: {len(tracks)}"
        cv2.rectangle(frame, (0, 0), (W, 52), (20, 20, 20), -1)
        cv2.putText(frame, hud, (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        writer.write(cv2.resize(frame, (ow, oh)) if scale != 1.0 else frame)
        frames += 1
        idx += 1
    cap.release()
    writer.release()

    # --- smooth camera-view heatmap: foot points blurred and glowing over the first frame
    acc = np.zeros((H, W), np.float32)
    for u, v in feet:
        if 0 <= int(v) < H and 0 <= int(u) < W:
            acc[int(v), int(u)] += 1
    acc = cv2.GaussianBlur(acc, (0, 0), sigmaX=W / 60)
    norm = (acc / acc.max() * 255).astype(np.uint8) if acc.max() > 0 else acc.astype(np.uint8)
    heat = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
    alpha = np.clip((norm.astype(np.float32) / 255 - 0.03) / 0.97, 0, 1)[..., None] ** 0.6 * 0.8
    base = first_frame if first_frame is not None else np.zeros((H, W, 3), np.uint8)
    cam = (base * (1 - alpha) + heat * alpha).astype(np.uint8)
    cv2.imwrite(str(out_dir / "heatmap_camera.png"), cv2.resize(cam, (ow, oh)))

    # --- smooth floor heatmap over the office plan (20 px per metre, 40 x 25 m)
    ppm = 20
    fw, fh = 40 * ppm, 25 * ppm
    facc = np.zeros((fh, fw), np.float32)
    for x, y in floor:
        px, py = int(x * ppm), int(y * ppm)
        if 0 <= px < fw and 0 <= py < fh:
            facc[py, px] += 1
    facc = cv2.GaussianBlur(facc, (0, 0), sigmaX=ppm * 0.6)
    fn = (facc / facc.max() * 255).astype(np.uint8) if facc.max() > 0 else facc.astype(np.uint8)
    fheat = cv2.applyColorMap(fn, cv2.COLORMAP_INFERNO)
    fa = np.clip((fn.astype(np.float32) / 255 - 0.04) / 0.96, 0, 1)[..., None] ** 0.8  # faint tails fade out, no grey smear
    plan = np.full((fh, fw, 3), 246, np.uint8)
    img = (plan * (1 - fa) + fheat * fa).astype(np.uint8)
    for z in zones:
        pts = np.array([[int(px * ppm), int(py * ppm)] for px, py in z["poly"]], np.int32)
        cv2.polylines(img, [pts], True, (140, 93, 47), 2)
        x0, y0 = pts[:, 0].min() + 8, pts[:, 1].min() + 24
        cv2.putText(img, z["name"], (int(x0), int(y0)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (56, 41, 28), 2)
    cv2.imwrite(str(out_dir / "heatmap_floor.png"), img)
    return {"video": str(path), "codec": codec, "frames_written": frames, "tracks": len(tracks),
            "positions": len(feet), "out_fps": round(out_fps, 2), "size": [ow, oh]}
