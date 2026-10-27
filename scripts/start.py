# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Start Office Vision Twin (after `python scripts/setup.py`). Windows, macOS, Linux.

    python scripts/start.py                  # uses backend/.env (HOST/PORT/OVT_API_TOKEN ...)
    python scripts/start.py --env-file ../data/sites/wisenet/site.env   # another site's settings

Refuses to listen on the network without an access token. Ctrl+C stops it.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
PY = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def read_env(path: Path) -> dict:
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split(" #", 1)[0].strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().upper()] = v.strip().strip('"').strip("'")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=".env", help="relative to backend/ (default .env)")
    args = ap.parse_args()
    if not PY.exists():
        sys.exit("Not set up yet: run  python scripts/setup.py")

    env_path = (BACKEND / args.env_file).resolve()
    cfg = {**read_env(env_path), **{k: v for k, v in os.environ.items() if k in ("HOST", "PORT", "OVT_API_TOKEN", "API_TOKEN")}}
    host, port = cfg.get("HOST", "127.0.0.1"), cfg.get("PORT", "8000")
    token = cfg.get("OVT_API_TOKEN") or cfg.get("API_TOKEN")
    if host not in LOOPBACK and not token:
        sys.exit(f"HOST={host} would expose the system on the network without an access token. "
                 f"Set OVT_API_TOKEN in {env_path} (python scripts/setup.py generates one).")

    cmd = [str(PY), "-m", "uvicorn", "src.main:app", "--host", host, "--port", str(port), "--proxy-headers"]
    if env_path.is_file():
        cmd += ["--env-file", str(env_path)]
    print(f"Office Vision Twin -> http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}  "
          f"({'token sign-in' if token else 'local access only'}).  Ctrl+C to stop.", flush=True)
    try:
        sys.exit(subprocess.call(cmd, cwd=BACKEND))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
