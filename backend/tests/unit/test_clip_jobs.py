from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest

from src.vision.clip_jobs import ClipJobManager, JobConflictError, JobNotFoundError
from src.vision.clip_runner import ClipRunResult, VideoClipRunner
from src.vision.clips import ClipConfig
from src.vision.tracking.fake import ScriptedTracker


def test_clip_jobs_start_and_completion(tmp_path: Path):
    """Test standard job launch and completion status recording."""
    mock_runner = MagicMock(spec=VideoClipRunner)
    mock_runner.run.return_value = ClipRunResult(
        run_id="run_test",
        clip_name="test_clip",
        camera_id="cam_01",
        status="completed",
        frames_read=10,
        frames_processed=5,
        source_fps=10.0,
        stride=2,
        observations=5,
        unique_tracks=1,
        events_emitted=5,
        event_type_counts={"PERSON_DETECTED": 5},
        incident_ids=[],
        timings={"decode_ms": {"mean": 1.0, "p50": 1.0, "p95": 1.0}},
        wall_seconds=0.5,
        effective_fps=10.0,
        errors=[],
        warnings=[],
    )

    manager = ClipJobManager(runner=mock_runner)
    config = ClipConfig(
        name="test_clip",
        video_file="test.mp4",
        camera_id="cam_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )

    run_id = manager.start(config=config, tracker_factory=lambda: ScriptedTracker({}))
    thread = manager.get_thread(run_id)
    assert thread is not None
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    record = manager.get(run_id)
    assert record is not None
    assert record["status"] == "completed"
    assert record["result"]["frames_read"] == 10
    assert manager.active_run_id is None


def test_clip_jobs_single_active_run_conflict(tmp_path: Path):
    """At most one active run allowed at a time; raises JobConflictError."""
    # Runner that blocks until an event is released
    block_event = threading.Event()

    def blocking_run(*args, **kwargs):
        block_event.wait(timeout=5.0)
        return ClipRunResult(
            run_id="run_1",
            clip_name="test_clip",
            camera_id="cam_01",
            status="completed",
            frames_read=1,
            frames_processed=1,
            source_fps=10.0,
            stride=1,
            observations=1,
            unique_tracks=1,
            events_emitted=1,
            event_type_counts={},
            incident_ids=[],
            timings={},
            wall_seconds=0.1,
            effective_fps=10.0,
            errors=[],
            warnings=[],
        )

    mock_runner = MagicMock(spec=VideoClipRunner)
    mock_runner.run.side_effect = blocking_run

    manager = ClipJobManager(runner=mock_runner)
    config = ClipConfig(
        name="test_clip",
        video_file="test.mp4",
        camera_id="cam_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )

    run_id_1 = manager.start(config=config, tracker_factory=lambda: ScriptedTracker({}))

    # Starting second run while run 1 is active must raise JobConflictError
    with pytest.raises(JobConflictError, match="already active"):
        manager.start(config=config, tracker_factory=lambda: ScriptedTracker({}))

    # Release first run and join
    block_event.set()
    thread = manager.get_thread(run_id_1)
    if thread:
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    assert manager.active_run_id is None


def test_clip_jobs_failing_factory_status_failed(tmp_path: Path):
    """A failing tracker_factory sets status 'failed' and never crashes the server."""
    mock_runner = MagicMock(spec=VideoClipRunner)
    manager = ClipJobManager(runner=mock_runner)
    config = ClipConfig(
        name="test_clip",
        video_file="test.mp4",
        camera_id="cam_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )

    def failing_factory():
        raise RuntimeError("Weights file corrupted")

    run_id = manager.start(config=config, tracker_factory=failing_factory)
    thread = manager.get_thread(run_id)
    assert thread is not None
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    record = manager.get(run_id)
    assert record is not None
    assert record["status"] == "failed"
    assert "Weights file corrupted" in record["error"]
    assert manager.active_run_id is None


def test_clip_jobs_cancel_active(tmp_path: Path):
    """Cancelling an active run sets cancel_event and marks status cancelled."""
    cancel_seen = threading.Event()

    def run_with_cancel(config, tracker, run_id, cancel_event=None, progress_cb=None):
        if cancel_event:
            cancel_event.wait(timeout=5.0)
            if cancel_event.is_set():
                cancel_seen.set()
                return ClipRunResult(
                    run_id=run_id,
                    clip_name=config.name,
                    camera_id=config.camera_id,
                    status="cancelled",
                    frames_read=0,
                    frames_processed=0,
                    source_fps=10.0,
                    stride=1,
                    observations=0,
                    unique_tracks=0,
                    events_emitted=0,
                    event_type_counts={},
                    incident_ids=[],
                    timings={},
                    wall_seconds=0.1,
                    effective_fps=0.0,
                    errors=[],
                    warnings=[],
                )
        raise RuntimeError("Should not reach here")

    mock_runner = MagicMock(spec=VideoClipRunner)
    mock_runner.run.side_effect = run_with_cancel

    manager = ClipJobManager(runner=mock_runner)
    config = ClipConfig(
        name="test_clip",
        video_file="test.mp4",
        camera_id="cam_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )

    run_id = manager.start(config=config, tracker_factory=lambda: ScriptedTracker({}))
    manager.cancel(run_id)

    thread = manager.get_thread(run_id)
    if thread:
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    assert cancel_seen.is_set()
    record = manager.get(run_id)
    assert record["status"] == "cancelled"


def test_clip_jobs_cancel_finished_conflict(tmp_path: Path):
    """Cancelling a completed run raises JobConflictError."""
    mock_runner = MagicMock(spec=VideoClipRunner)
    mock_runner.run.return_value = ClipRunResult(
        run_id="run_done",
        clip_name="test_clip",
        camera_id="cam_01",
        status="completed",
        frames_read=5,
        frames_processed=5,
        source_fps=10.0,
        stride=1,
        observations=1,
        unique_tracks=1,
        events_emitted=1,
        event_type_counts={},
        incident_ids=[],
        timings={},
        wall_seconds=0.1,
        effective_fps=10.0,
        errors=[],
        warnings=[],
    )

    manager = ClipJobManager(runner=mock_runner)
    config = ClipConfig(
        name="test_clip",
        video_file="test.mp4",
        camera_id="cam_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )

    run_id = manager.start(config=config, tracker_factory=lambda: ScriptedTracker({}))
    thread = manager.get_thread(run_id)
    if thread:
        thread.join(timeout=5.0)

    with pytest.raises(JobConflictError, match="already finished"):
        manager.cancel(run_id)


def test_clip_jobs_unknown_id(tmp_path: Path):
    """Unknown run_id raises JobNotFoundError on cancel, returns None on get."""
    mock_runner = MagicMock(spec=VideoClipRunner)
    manager = ClipJobManager(runner=mock_runner)

    assert manager.get("nonexistent_id") is None
    with pytest.raises(JobNotFoundError):
        manager.cancel("nonexistent_id")
