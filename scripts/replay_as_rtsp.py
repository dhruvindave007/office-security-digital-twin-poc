# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Publish recorded camera videos as LIVE RTSP cameras (the same protocol real IP cameras use).

Starts a local MediaMTX RTSP server and one ffmpeg per video, each looping at real speed:
    rtsp://127.0.0.1:8554/cam_1 ... cam_6
Then point the system at them (data/sites/wisenet/live_rtsp.yaml) or open one in VLC.

    python scripts/replay_as_rtsp.py               # WiseNET set 5, all its cameras
    python scripts/replay_as_rtsp.py --set 9
Stop with Ctrl+C. Needs ffmpeg on PATH and tools/mediamtx/mediamtx.exe
(https://github.com/bluenviron/mediamtx/releases, MIT licence).
"""
import argparse
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETS = REPO / "data" / "datasets" / "wisenet" / "wisenet_dataset" / "video_sets"
MTX = REPO / "tools" / "mediamtx" / ("mediamtx.exe" if sys.platform == "win32" else "mediamtx")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=5)
    ap.add_argument("--port", type=int, default=8554)
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH"); return 1
    if not MTX.exists():
        print(f"MediaMTX not found at {MTX}"); return 1
    videos = sorted((SETS / f"set_{a.set}").glob(f"video{a.set}_*.avi"))
    if not videos:
        print(f"no videos for set {a.set} in {SETS}"); return 1
    procs = [subprocess.Popen([str(MTX)], cwd=str(MTX.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    time.sleep(1.5)
    for v in videos:
        cam = "cam_" + v.stem.split("_")[-1]
        # Re-encode to H.264 (what IP cameras send); -re = real-time speed; loop forever.
        procs.append(subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-re", "-stream_loop", "-1", "-i", str(v), "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", "25", "-pix_fmt", "yuv420p",
             "-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://127.0.0.1:{a.port}/{cam}"]))
        print(f"  {cam}: rtsp://127.0.0.1:{a.port}/{cam}  <- {v.name}")
    print("streaming; Ctrl+C to stop")

    def shutdown(*_):
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    while True:
        time.sleep(2)
        dead = [p for p in procs if p.poll() is not None]
        if dead:
            print(f"{len(dead)} streaming process(es) exited; stopping"); shutdown()


if __name__ == "__main__":
    sys.exit(main())
