# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import threading
from typing import Optional
import pytest
from fastapi.testclient import TestClient
import yaml

from src.config import get_settings
from src.main import app
from src.vision.tracking.fake import ScriptedTracker
from tests.synthetic_video import generate_synthetic_video


@pytest.fixture
def clips_setup(tmp_path: Path, monkeypatch):
    """Set up a temporary clips directory and configure settings."""
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    db_path = tmp_path / "test_api_clips.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("VISION_CLIPS_DIR", str(clips_dir))
    get_settings.cache_clear()

    # Generate a real synthetic video
    video_path, _ = generate_synthetic_video(clips_dir, stem="test_clip", duration_sec=0.4, fps=10.0)

    # Write valid clip manifest
    manifest_data = {
        "name": "test_clip",
        "description": "API test clip",
        "video_file": video_path.name,
        "camera_id": "cam_reception_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "process_fps": 5.0,
        "source_note": "Synthetic API test",
    }
    manifest_path = clips_dir / "test_clip.yaml"
    manifest_path.write_text(yaml.dump(manifest_data), encoding="utf-8")

    yield clips_dir, video_path

    get_settings.cache_clear()


def test_api_list_clips(clips_setup):
    """GET /api/vision/clips returns metadata and video_present."""
    clips_dir, _ = clips_setup

    with TestClient(app) as client:
        res = client.get("/api/vision/clips")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
        clip = next((c for c in data if c["name"] == "test_clip"), None)
        assert clip is not None
        assert clip["camera_id"] == "cam_reception_01"
        assert clip["video_present"] is True


def test_api_run_clip_completion_polling(clips_setup):
    """POST /api/vision/clips/{name}/run returns 202 and completes successfully."""
    clips_dir, _ = clips_setup

    with TestClient(app) as client:
        # Override tracker factory to return ScriptedTracker
        app.state.tracker_factory = lambda: ScriptedTracker({})

        # Trigger run
        res = client.post("/api/vision/clips/test_clip/run")
        assert res.status_code == 202
        run_id = res.json()["run_id"]
        assert run_id.startswith("run_")

        # Join background thread
        thread = app.state.clip_job_manager.get_thread(run_id)
        assert thread is not None
        thread.join(timeout=5.0)
        assert not thread.is_alive()

        # Poll run status
        poll_res = client.get(f"/api/vision/clips/runs/{run_id}")
        assert poll_res.status_code == 200
        status_data = poll_res.json()
        assert status_data["status"] == "completed"
        assert status_data["result"]["frames_read"] == 4
        assert status_data["result"]["frames_processed"] == 2


def test_api_run_clip_error_codes(clips_setup):
    """Verify 404, 422, 409, and 503 error responses."""
    clips_dir, _ = clips_setup

    with TestClient(app) as client:
        app.state.tracker_factory = lambda: ScriptedTracker({})

        # 1. 404: Unknown clip
        res_404 = client.post("/api/vision/clips/unknown_clip/run")
        assert res_404.status_code == 404

        # 2. 422: Video file missing
        missing_manifest = {
            "name": "missing_vid",
            "video_file": "missing.mp4",
            "camera_id": "cam_reception_01",
            "base_time_utc": "2026-01-20T09:30:00Z",
            "source_note": "test",
        }
        (clips_dir / "missing_vid.yaml").write_text(yaml.dump(missing_manifest), encoding="utf-8")
        res_422 = client.post("/api/vision/clips/missing_vid/run")
        assert res_422.status_code == 422

        # 3. 409: Another run active
        block_event = threading.Event()
        orig_runner_run = app.state.clip_runner.run

        def blocking_run(*args, **kwargs):
            block_event.wait(timeout=5.0)
            return orig_runner_run(*args, **kwargs)

        app.state.clip_runner.run = blocking_run
        try:
            res_first = client.post("/api/vision/clips/test_clip/run")
            assert res_first.status_code == 202
            run_id = res_first.json()["run_id"]

            res_409 = client.post("/api/vision/clips/test_clip/run")
            assert res_409.status_code == 409
        finally:
            block_event.set()
            thread = app.state.clip_job_manager.get_thread(run_id)
            if thread:
                thread.join(timeout=5.0)
            app.state.clip_runner.run = orig_runner_run

        # 4. 503: Pipeline not initialized
        orig_pipeline = app.state.observation_pipeline
        try:
            app.state.observation_pipeline = None
            res_503 = client.post("/api/vision/clips/test_clip/run")
            assert res_503.status_code == 503
        finally:
            app.state.observation_pipeline = orig_pipeline


def test_api_cancel_clip_run(clips_setup):
    """Verify cancellation: 202, 404, and 409 status codes."""
    clips_dir, _ = clips_setup

    with TestClient(app) as client:
        app.state.tracker_factory = lambda: ScriptedTracker({})

        # 404 for unknown run_id
        res_unknown = client.post("/api/vision/clips/runs/run_unknown/cancel")
        assert res_unknown.status_code == 404

        # Start a run that waits for cancel
        cancel_seen = threading.Event()
        orig_runner_run = app.state.clip_runner.run

        def cancellable_run(*args, **kwargs):
            cancel_evt = kwargs.get("cancel_event")
            if cancel_evt:
                cancel_evt.wait(timeout=5.0)
                cancel_seen.set()
            return orig_runner_run(*args, **kwargs)

        app.state.clip_runner.run = cancellable_run
        try:
            res = client.post("/api/vision/clips/test_clip/run")
            assert res.status_code == 202
            run_id = res.json()["run_id"]

            # Cancel active run
            res_cancel = client.post(f"/api/vision/clips/runs/{run_id}/cancel")
            assert res_cancel.status_code == 202
            assert res_cancel.json()["status"] == "cancelling"

            thread = app.state.clip_job_manager.get_thread(run_id)
            if thread:
                thread.join(timeout=5.0)

            # 409 on already finished run
            res_409 = client.post(f"/api/vision/clips/runs/{run_id}/cancel")
            assert res_409.status_code == 409
        finally:
            app.state.clip_runner.run = orig_runner_run


def test_api_failing_factory_server_stays_up(clips_setup):
    """A failing tracker_factory makes status failed while server stays responsive."""
    clips_dir, _ = clips_setup

    with TestClient(app) as client:
        def failing_factory():
            raise RuntimeError("Hardware device out of memory")

        app.state.tracker_factory = failing_factory

        res = client.post("/api/vision/clips/test_clip/run")
        assert res.status_code == 202
        run_id = res.json()["run_id"]

        thread = app.state.clip_job_manager.get_thread(run_id)
        if thread:
            thread.join(timeout=5.0)
            assert not thread.is_alive()

        poll_res = client.get(f"/api/vision/clips/runs/{run_id}")
        assert poll_res.status_code == 200
        data = poll_res.json()
        assert data["status"] == "failed"
        assert "Hardware device out of memory" in data["error"]

        # Health endpoint responds 200 OK
        health_res = client.get("/health")
        assert health_res.status_code == 200


def test_api_run_clip_unknown_camera_is_422(clips_setup):
    """A clip whose camera_id is not calibrated is rejected before any background run starts."""
    clips_dir, video_path = clips_setup
    bad = {
        "name": "bad_cam_clip",
        "video_file": video_path.name,
        "camera_id": "cam_does_not_exist",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "test",
    }
    (clips_dir / "bad_cam_clip.yaml").write_text(yaml.dump(bad), encoding="utf-8")

    with TestClient(app) as client:
        res = client.post("/api/vision/clips/bad_cam_clip/run")
        assert res.status_code == 422
        assert "cam_does_not_exist" in res.json()["detail"]
        assert app.state.clip_job_manager.active_run_id is None
