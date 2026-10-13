import os
from pathlib import Path
import subprocess
import sys
import pytest


def _run_import(module_statement: str):
    backend_dir = Path(__file__).resolve().parent.parent.parent
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [sys.executable, "-c", module_statement],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"FAILED import: {module_statement}")
        print("STDOUT:\n", proc.stdout)
        print("STDERR:\n", proc.stderr)
    assert proc.returncode == 0, f"Import '{module_statement}' failed:\n{proc.stderr}"


def test_import_src_main():
    _run_import("import src.main")


def test_import_src_rules_engine():
    _run_import("import src.rules.engine")


def test_import_src_vision_pipeline():
    _run_import("import src.vision.pipeline")


def test_import_src_access_service():
    _run_import("import src.access.service")


def test_import_src_rules_correlation():
    _run_import("import src.rules.correlation")


def test_import_src_api_routes_access():
    _run_import("import src.api.routes_access")


def test_import_src_main_no_vision_heavy_libs():
    _run_import("import src.main, sys; assert not ({'cv2', 'torch', 'ultralytics'} & set(sys.modules))")



