# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Soak test: run the real server in live mode for N minutes and record stability every minute.

Measures: server memory (RSS), per-camera processed fps, end-to-end latency, skipped slots,
reconnects, active tracks, incidents, events written and database size. From backend/:
  .\\.venv\\Scripts\\python.exe -m evals.perf.soak_live --minutes 20
Writes data/benchmarks/soak_<timestamp>.md
"""
import argparse, json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PORT = 8013


def get(p):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{p}", timeout=10))


def rss_mb(_pid=None):
    # The venv's python.exe on Windows is a launcher stub; measure the process that owns the port.
    cmd = f"(Get-Process -Id (Get-NetTCPConnection -LocalPort {PORT} -State Listen).OwningProcess).WorkingSet64"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()
    return round(int(out) / 2**20) if out.isdigit() else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--env", default="../data/sites/wisenet/site.env"); a = ap.parse_args()
    db = BACKEND / "data" / "soak_live.db"
    for f in BACKEND.glob("data/soak_live.db*"):
        f.unlink()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["DATABASE_URL"] = "sqlite:///./data/soak_live.db"
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.main:app", "--env-file", a.env, "--port", str(PORT)],
                           cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rows = []
    try:
        for _ in range(120):
            try:
                if all(c["state"] == "running" for c in get("/api/live/status")["cameras"]):
                    break
            except Exception:
                pass
            time.sleep(1)
        t0 = time.time()
        for m in range(a.minutes + 1):
            s = get("/api/live/status")
            cams = s["cameras"]
            e2e = [c["latency_ms"]["end_to_end_ms"]["p95"] or 0 for c in cams]
            rows.append({"min": m, "rss_mb": rss_mb(srv.pid), "fps": s["totals"]["processed_fps"],
                         "e2e_p95_max": max(e2e), "missed": s["totals"]["missed_slots"],
                         "reconnects": sum(c["source"]["reconnects"] for c in cams),
                         "tracks": get("/api/twin/state")["total_active_tracks"],
                         "incidents": get("/api/twin/incidents?limit=1")["total"],
                         "db_mb": round(sum(f.stat().st_size for f in BACKEND.glob("data/soak_live.db*")) / 2**20, 1),
                         "badges": (s.get("badge_replay") or {}).get("swipes_fired")})
            print(rows[-1], flush=True)
            if m < a.minutes:
                time.sleep(max(0, t0 + 60 * (m + 1) - time.time()))
    finally:
        srv.terminate()
        try:
            srv.wait(20)
        except subprocess.TimeoutExpired:
            srv.kill()
    import sqlite3
    con = sqlite3.connect(db)
    events = dict(con.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type").fetchall())
    con.close()
    out = BACKEND / "data" / "benchmarks" / f"soak_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Live soak test ({a.minutes} min, {a.env})", "",
             "| min | RSS MB | fps (all cams) | worst cam e2e p95 ms | skipped slots | reconnects | tracks | incidents | DB MB | badge swipes |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['min']} | {r['rss_mb']} | {r['fps']} | {r['e2e_p95_max']} | {r['missed']} | {r['reconnects']} | {r['tracks']} | {r['incidents']} | {r['db_mb']} | {r['badges']} |" for r in rows]
    lines += ["", f"Events written by type: {events}"]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
