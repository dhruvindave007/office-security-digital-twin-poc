# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Interruption test for the dashboard: camera streams cut and restored, then the backend restarted.

Starts its own RTSP replay (scripts/replay_as_rtsp.py) and backend (live_rtsp.yaml) on --port, opens the
dashboard in a real browser (Playwright + installed Edge) and checks what the operator sees:
  1. all streams killed  -> tiles leave LIVE / show no fresh picture, top bar counts fewer cameras
  2. streams restarted   -> tiles LIVE with fresh pictures again, no page reload
  3. backend killed      -> push channel shows RECONNECTING and the interruption banner
  4. backend restarted   -> push channel LIVE again, data resynchronised, no page reload
Internal use only (WiseNET footage). Leaves nothing running.

    backend\\.venv\\Scripts\\python.exe scripts\\e2e_interruptions.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
PY = sys.executable
PORT = 8015
BASE = f"http://127.0.0.1:{PORT}"
RESULTS = []
_starts = 0
STARTED = []   # every process this script started, killed at the end even if a start-up fails


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"[{' ok ' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}", flush=True)


def kill_tree(proc):
    if proc and proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def start_replay():
    p = subprocess.Popen([PY, str(REPO / "scripts" / "replay_as_rtsp.py"), "--set", "5"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    STARTED.append(p)
    time.sleep(5)
    return p


def start_backend():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"LIVE_CONFIG_PATH": "../data/sites/wisenet/live_rtsp.yaml", "AUTO_INVESTIGATE_ENABLED": "false",
                "DATABASE_URL": "sqlite:///./data/e2e_interrupt.db"})
    global _starts
    _starts += 1
    log = open(BACKEND / "data" / f"e2e_interrupt_backend_{_starts}.log", "w")
    p = subprocess.Popen([PY, "-m", "uvicorn", "src.main:app", "--env-file", "../data/sites/wisenet/site.env",
                          "--host", "127.0.0.1", "--port", str(PORT)], cwd=BACKEND, env=env, stdout=log, stderr=log)
    STARTED.append(p)
    for _ in range(120):
        try:
            cams = json.load(urllib.request.urlopen(BASE + "/api/live/status", timeout=3))["cameras"]
            if cams and all(c["state"] == "running" and c["source"]["state"] == "live" for c in cams):
                return p
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"backend/cameras did not come up (see backend/data/e2e_interrupt_backend_{_starts}.log)")


def tiles(page):
    return {t.get_attribute("data-testid")[7:]: (t.get_attribute("data-picture"), "LIVE" in t.inner_text())
            for t in page.locator('[data-testid^="camera-"]').all()}


def wait(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(1)
    return False


def main() -> int:
    for f in BACKEND.glob("data/e2e_interrupt.db*"):
        f.unlink()
    replay = start_replay()
    backend = start_backend()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            navs = []
            page.on("framenavigated", lambda f: f == page.main_frame and navs.append(f.url))
            page.goto(BASE + "/")
            page.wait_for_function("document.querySelector('[data-testid=conn]')?.innerText.includes('LIVE')", timeout=30000)
            ok = wait(lambda: all(v == ("fresh", True) for v in tiles(page).values()), 30)
            check("start: 6 tiles LIVE with fresh pictures over RTSP", ok, str(tiles(page)))
            nav0 = len(navs)

            # 1. cut every camera stream
            kill_tree(replay)
            t0 = time.time()
            ok = wait(lambda: all(not live for _, live in tiles(page).values()), 30)
            check("streams cut: every tile leaves LIVE", ok, f"after {time.time() - t0:.0f} s: {tiles(page)}")
            ok = wait(lambda: all(pic != "fresh" for pic, _ in tiles(page).values()), 15)
            check("streams cut: tiles stop claiming a fresh picture", ok, str(tiles(page)))
            top = page.inner_text('header[role="banner"]')
            check("top bar shows fewer cameras live", "0/6 cameras live" in top, top.split("cameras live")[0][-6:] + "cameras live")
            page.screenshot(path=os.path.join(os.environ.get("TEMP", "."), "interrupt_cameras_down.png"))

            # 2. restore the streams
            replay = start_replay()
            t0 = time.time()
            ok = wait(lambda: all(v == ("fresh", True) for v in tiles(page).values()), 60)
            check("streams back: all tiles LIVE with fresh pictures, no reload", ok and len(navs) == nav0,
                  f"after {time.time() - t0:.0f} s; reloads {len(navs) - nav0}")

            # 3. backend down
            kill_tree(backend)
            ok = wait(lambda: "RECONNECTING" in page.inner_text('[data-testid="conn"]') or "OFFLINE" in page.inner_text('[data-testid="conn"]'), 15)
            check("backend down: push channel shows RECONNECTING", ok, page.inner_text('[data-testid="conn"]'))
            check("backend down: interruption banner shown", page.locator('[data-testid="conn-banner"]').count() == 1)

            # 4. backend back
            backend = start_backend()
            ok = wait(lambda: "LIVE" in page.inner_text('[data-testid="conn"]') and page.locator('[data-testid="conn-banner"]').count() == 0, 60)
            check("backend back: push channel LIVE, banner gone, no reload", ok and len(navs) == nav0, page.inner_text('[data-testid="conn"]'))
            ok = wait(lambda: all(v == ("fresh", True) for v in tiles(page).values()), 60)
            check("backend back: camera tiles live again", ok, str(tiles(page)))
            browser.close()
    finally:
        for proc in STARTED:
            kill_tree(proc)
        subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process mediamtx -EA SilentlyContinue | Stop-Process -Force"], capture_output=True)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
