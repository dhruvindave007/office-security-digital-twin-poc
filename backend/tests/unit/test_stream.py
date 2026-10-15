"""SSE push channel against a real server (TestClient buffers whole responses, so it cannot test
an endless stream): state snapshots arrive on a timer; incidents arrive as the rules raise them."""
import json
import socket
import threading
import time

import httpx
import uvicorn

from src.main import app


def _events(lines):
    ev = None
    for line in lines:
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: ") and ev:
            yield ev, json.loads(line[6:])
            ev = None


def test_stream_pushes_state_and_incidents():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        with httpx.stream("GET", base + "/api/stream?state_every=0.25", timeout=20) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            it = _events(r.iter_lines())
            ev, data = next(it)
            assert ev == "state" and "zone_occupancy" in data
            res = httpx.post(base + "/api/simulator/scenarios/tailgating/run", timeout=20)
            assert res.status_code == 200
            want, got = set(res.json()["incidents"]), set()
            deadline = time.time() + 10
            for ev, data in it:
                if ev == "incident":
                    got.add(data["incident_id"])
                if want <= got or time.time() > deadline:
                    break
            assert want and want <= got
    finally:
        server.should_exit = True
        th.join(10)
