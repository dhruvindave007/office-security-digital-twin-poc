"""One-time setup on Windows, macOS or Linux. Needs only Python 3.11+ and Node.js 20+ on PATH.

    python scripts/setup.py              # core system: twin, rules, simulator, dashboard, AI investigation
    python scripts/setup.py --vision     # + camera person detection (Ultralytics YOLO, OpenVINO)

Creates backend/.venv, installs the backend, builds the dashboard, and creates backend/.env with a
freshly generated access token (only if backend/.env does not exist; an existing one is never touched).
Safe to run again. Then start the system with:  python scripts/start.py
"""
import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND, FRONTEND = REPO / "backend", REPO / "frontend"
VENV = BACKEND / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def step(msg):
    print(f"\n==> {msg}", flush=True)


def run(cmd, cwd):
    print("   $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, shell=(os.name == "nt" and str(cmd[0]).startswith("npm")))


def fail(msg):
    print(f"\n[setup] {msg}", file=sys.stderr)
    sys.exit(1)


def _windows_long_paths() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vision", action="store_true", help="also install camera detection (large download)")
    ap.add_argument("--skip-frontend", action="store_true", help="API only; do not build the dashboard")
    args = ap.parse_args()

    if sys.version_info < (3, 11):
        fail(f"Python 3.11+ required (this is {sys.version.split()[0]}).")
    if os.name == "nt" and len(str(VENV)) > 120 and not _windows_long_paths():
        # Some installed files sit ~140 characters below .venv; past Windows' 260-character limit they
        # cannot be opened and imports fail with confusing "circular import" errors.
        fail(f"This folder's path is too long for Windows ({len(str(REPO))} characters). Clone the repository "
             "to a shorter path such as C:\\ovt, or enable long paths: https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation")
    if not args.skip_frontend and not shutil.which("npm"):
        fail("Node.js 20+ (npm) is required to build the dashboard: https://nodejs.org  (or use --skip-frontend)")

    step("Python environment (backend/.venv)")
    uv = shutil.which("uv")
    if not PY.exists():
        run([uv, "venv", VENV, "--python", sys.executable] if uv else [sys.executable, "-m", "venv", VENV], BACKEND)
    extras = "dev,vision" if args.vision else "dev"
    if uv:
        run([uv, "pip", "install", "--python", PY, "-e", f".[{extras}]"], BACKEND)
    else:
        run([PY, "-m", "pip", "install", "--upgrade", "pip"], BACKEND)
        run([PY, "-m", "pip", "install", "-e", f".[{extras}]"], BACKEND)

    if not args.skip_frontend:
        step("Operator dashboard (frontend/dist)")
        run(["npm", "ci", "--no-audit", "--no-fund"], FRONTEND)
        run(["npm", "run", "build"], FRONTEND)

    step("Configuration (backend/.env)")
    env = BACKEND / ".env"
    token = None
    if env.exists():
        print("   backend/.env already exists: left unchanged.")
    else:
        token = secrets.token_urlsafe(32)
        text = (BACKEND / ".env.example").read_text(encoding="utf-8").replace("OVT_API_TOKEN=\n", f"OVT_API_TOKEN={token}\n", 1)
        env.write_text(text, encoding="utf-8")
        try:
            os.chmod(env, 0o600)          # owner-only on macOS/Linux; Windows keeps the user-profile ACL
        except OSError:
            pass
        print("   Created backend/.env with a new access token.")
    (BACKEND / "data").mkdir(exist_ok=True)

    step("Self-check")
    run([PY, "-c", "import src.main; print('   backend imports OK')"], BACKEND)

    print("\nSetup complete.  Start:  python scripts/start.py   then open http://127.0.0.1:8000")
    if token:
        print("\nOperator access token (shown once; also stored in backend/.env):\n\n   " + token + "\n")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        fail(f"Step failed (exit {e.returncode}): {' '.join(map(str, e.cmd))}")
