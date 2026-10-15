"""One command to run the whole system as the WiseNET building with live cameras.

    backend\\.venv\\Scripts\\python.exe scripts\\demo.py            # 6 cameras replayed live from files
    backend\\.venv\\Scripts\\python.exe scripts\\demo.py --rtsp     # same, but over RTSP like IP cameras
    backend\\.venv\\Scripts\\python.exe scripts\\demo.py --no-ai    # no automatic Bedrock investigations

It checks every prerequisite, starts the backend, and only reports "READY" after verifying that
the API answers, every camera is live and processing, a camera picture can be fetched and the
push channel delivers state. Ctrl+C stops everything. Internal use only (WiseNET: CC BY-NC-SA 4.0).
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
PY = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
SITE = REPO / "data" / "sites" / "wisenet"
MODELS = BACKEND / "data" / "models"
FRONTEND = REPO / "frontend"
OK, WARN, FAIL = "  ok  ", " warn ", " FAIL "


def line(tag, msg):
    print(f"[{tag}] {msg}", flush=True)


def port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def get(url, timeout=5):
    return urllib.request.urlopen(url, timeout=timeout)


def check_prereqs(args) -> bool:
    good = True
    if PY.exists():
        line(OK, f"Python environment: {PY}")
    else:
        line(FAIL, f"Python environment missing at {PY} (see README: uv venv + uv pip install -e .[vision])"); good = False
    if shutil.which("ffmpeg"):
        line(OK, "ffmpeg found (evidence clips, RTSP replay)")
    else:
        line(WARN if not args.rtsp else FAIL, "ffmpeg not on PATH: evidence clips fall back to large AVI files"
             + ("; --rtsp needs it" if args.rtsp else "")); good = good and not args.rtsp
    sets = REPO / "data" / "datasets" / "wisenet" / "wisenet_dataset" / "video_sets" / "set_5"
    if len(list(sets.glob("video5_*.avi"))) >= 6:
        line(OK, "WiseNET set 5 footage (6 cameras)")
    else:
        line(FAIL, f"WiseNET footage missing in {sets}"); good = False
    model = MODELS / "yolo11s_480_openvino_model"
    if not model.exists():
        line(WARN, "detection model not exported yet; exporting yolo11s at 480x640 (one time, ~1 min)")
        r = subprocess.run([str(PY), "-c", "from ultralytics import YOLO; import shutil;"
                            "o=YOLO('yolo11s.pt').export(format='openvino', imgsz=[480,640]);"
                            "shutil.move(o, 'yolo11s_480_openvino_model')"], cwd=MODELS)
        if r.returncode != 0 or not model.exists():
            line(FAIL, "model export failed"); return False
    line(OK, f"detection model: {model.name}")
    if args.rtsp and not (REPO / "tools" / "mediamtx" / ("mediamtx.exe" if os.name == "nt" else "mediamtx")).exists():
        line(FAIL, "tools/mediamtx missing (download MediaMTX into tools/mediamtx for --rtsp)"); good = False
    if not args.no_ai:
        r = subprocess.run([str(PY), "-c", "import boto3; boto3.Session().client('sts', region_name='us-east-1').get_caller_identity()"],
                           capture_output=True, text=True, cwd=BACKEND)
        if r.returncode == 0:
            line(OK, "AWS signed in (automatic AI investigations on, budget $1/day)")
        else:
            line(WARN, "AWS not signed in: AI investigations will pause until you run "
                       "backend\\.venv\\Scripts\\python.exe scripts\\aws_sso_login.py (everything else works)")
    if not port_free(args.port):
        line(FAIL, f"port {args.port} is in use (another backend running?)"); good = False
    return good and check_dashboard(args)


def check_dashboard(args) -> bool:
    """The operator dashboard is served by the backend from frontend/dist; build it when missing or stale."""
    dist = FRONTEND / "dist" / "index.html"
    src_mtime = max((f.stat().st_mtime for f in (FRONTEND / "src").rglob("*") if f.is_file()), default=0)
    stale = not dist.exists() or dist.stat().st_mtime < max(src_mtime, (FRONTEND / "index.html").stat().st_mtime)
    if not stale:
        line(OK, "dashboard build up to date (frontend/dist)")
        return True
    if args.no_build:
        line(WARN if dist.exists() else FAIL, "dashboard build " + ("is older than its source" if dist.exists() else "missing")
             + " and --no-build was given")
        return dist.exists()
    npm = shutil.which("npm")
    if not npm:
        line(WARN if dist.exists() else FAIL, "npm not found: cannot (re)build the dashboard"
             + (" - serving the existing, older build" if dist.exists() else ""))
        return dist.exists()
    if not (FRONTEND / "node_modules").exists():
        line(WARN, "installing dashboard dependencies (npm install, one time)")
        if subprocess.run([npm, "install"], cwd=FRONTEND).returncode != 0:
            line(FAIL, "npm install failed"); return False
    line(WARN, "building the dashboard (npm run build, ~10 s)")
    r = subprocess.run([npm, "run", "build"], cwd=FRONTEND, capture_output=True, text=True)
    if r.returncode != 0 or not dist.exists():
        line(FAIL, "dashboard build failed:\n" + (r.stdout + r.stderr)[-1500:]); return False
    line(OK, "dashboard built (frontend/dist)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtsp", action="store_true", help="stream the cameras over RTSP (MediaMTX) like real IP cameras")
    ap.add_argument("--no-ai", action="store_true", help="disable automatic Bedrock investigations")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--fresh", action="store_true", help="start with an empty database and evidence folder")
    ap.add_argument("--no-build", action="store_true", help="do not (re)build the dashboard; serve whatever frontend/dist has")
    args = ap.parse_args()

    print("Office Vision Twin demo: WiseNET building, 6 live cameras\n")
    if not check_prereqs(args):
        print("\nNot starting: fix the FAIL items above."); return 1

    procs = []
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if args.no_ai:
        env["AUTO_INVESTIGATE_ENABLED"] = "false"
    if args.rtsp:
        env["LIVE_CONFIG_PATH"] = "../data/sites/wisenet/live_rtsp.yaml"
        procs.append(subprocess.Popen([str(PY), str(REPO / "scripts" / "replay_as_rtsp.py"), "--set", "5"]))
        time.sleep(4)
    if args.fresh:
        for p in BACKEND.glob("data/wisenet_live.db*"):
            p.unlink()
        shutil.rmtree(BACKEND / "data" / "evidence", ignore_errors=True)
    log = open(BACKEND / "data" / "demo_backend.log", "w")
    procs.append(subprocess.Popen([str(PY), "-m", "uvicorn", "src.main:app", "--env-file", "../data/sites/wisenet/site.env",
                                   "--host", "127.0.0.1", "--port", str(args.port)], cwd=BACKEND, env=env, stdout=log, stderr=log))

    def stop(*_):
        print("\nstopping...")
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    base = f"http://127.0.0.1:{args.port}"
    print("\nstarting backend (models compile on first use: ~20 s)...")
    deadline = time.time() + 120
    status = None
    while time.time() < deadline:
        if procs[-1].poll() is not None:
            line(FAIL, f"backend exited; see {BACKEND / 'data' / 'demo_backend.log'}"); stop()
        try:
            status = json.load(get(base + "/api/live/status"))
            cams = status["cameras"]
            if cams and all(c["state"] == "running" and c["source"]["state"] == "live" and c["frames_processed"] > 0 for c in cams):
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        line(FAIL, f"cameras did not all go live within 120 s; status: {json.dumps(status)[:300] if status else 'no answer'}")
        stop()

    # verify, don't assume
    checks = []
    checks.append(("API health", json.load(get(base + "/api/health"))["status"] == "ok"))
    checks.append((f"{len(status['cameras'])} cameras live and processing", True))
    jpg = get(base + f"/api/live/cameras/{status['cameras'][0]['camera_id']}/frame.jpg").read()
    checks.append(("camera picture (annotated JPEG)", jpg[:2] == b"\xff\xd8"))
    r = get(base + "/api/stream?state_every=0.5", timeout=10)
    got_state, t_end = False, time.time() + 8
    while not got_state and time.time() < t_end:
        got_state = r.readline().startswith(b"event: state")
    r.close()
    checks.append(("push channel (SSE) delivering twin state", got_state))
    page = get(base + "/").read()
    checks.append(("operator dashboard served at /", b'<div id="root">' in page))
    print()
    for name, ok in checks:
        line(OK if ok else FAIL, name)
    if not all(ok for _, ok in checks):
        stop()
    print(f"""
READY.
  Operator dashboard     : {base}/
  API + interactive docs : {base}/docs
  Live camera (MJPEG)    : {base}/api/live/cameras/cam_3/mjpeg     (cam_1 ... cam_6)
  Camera health          : {base}/api/live/status
  Incidents              : {base}/api/twin/incidents
  Auto-investigations    : {base}/api/twin/investigations/auto
  Push channel           : {base}/api/stream
  Backend log            : {BACKEND / 'data' / 'demo_backend.log'}
Ctrl+C to stop.""", flush=True)
    while True:
        time.sleep(2)
        if any(p.poll() is not None for p in procs):
            line(FAIL, "a process exited; stopping"); stop()


if __name__ == "__main__":
    sys.exit(main())
