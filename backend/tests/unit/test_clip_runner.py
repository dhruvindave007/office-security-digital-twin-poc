from pathlib import Path
import threading
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from src.access.config import load_access_config
from src.rules.correlation import CorrelationEngine
from src.rules.engine import IncidentRuleEngine
from src.rules.policy import load_access_policy
from src.twin.bootstrap import TwinBootstrapService
from src.vision.camera_graph import CameraNode
from src.vision.clip_runner import VideoClipRunner
from src.vision.clips import ClipConfig
from src.vision.geometry import BoundingBox2D
from src.vision.pipeline import ObservationPipeline
from src.vision.seed import VisionSeedLoader
from src.vision.tracking.base import TrackedDetection
from src.vision.tracking.fake import ScriptedTracker
from src.vision.zones import VisionZoneResolver
from tests.synthetic_video import generate_synthetic_video


def _setup_runner_and_twin(tmp_path: Path):
    """Set up a fully wired VideoClipRunner backed by a temporary SQLite database."""
    db_path = str(tmp_path / "test_twin_runner.db")
    twin_engine = TwinBootstrapService.bootstrap(
        db_path=db_path,
        seed_path="src/twin/floorplan_seed.yaml",
    )

    homography_mgr, camera_graph, topology = VisionSeedLoader.load_all()
    zone_resolver = VisionZoneResolver.from_twin_store(twin_engine.store)
    policy = load_access_policy("data/rules/access_policy.yaml")
    access_config = load_access_config("data/access/doors.yaml")

    correlation_engine = CorrelationEngine(
        policy=policy,
        access_config=access_config,
        topology=topology,
        store=twin_engine.store,
    )
    rule_engine = IncidentRuleEngine(
        twin_engine.store,
        twin_engine.state,
        policy,
        correlation=correlation_engine,
    )

    pipeline = ObservationPipeline(
        homography_manager=homography_mgr,
        zone_resolver=zone_resolver,
        twin_engine=twin_engine,
        topology_service=topology,
        rule_engine=rule_engine,
    )

    cameras = {c.camera_id: c for c in VisionSeedLoader.load_cameras()}
    runner = VideoClipRunner(
        pipeline=pipeline,
        camera_nodes=cameras,
        clips_dir=str(tmp_path),
    )
    return runner, pipeline, twin_engine


def test_runner_stride_and_frames_processed(tmp_path: Path):
    """Source 10 fps, process 5 fps gives stride=2 and processes every second frame."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="stride_clip", duration_sec=3.0, fps=10.0)
    runner, _, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="stride_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=5.0,
        source_note="test",
    )
    tracker = ScriptedTracker({})

    res = runner.run(config=config, tracker=tracker, run_id="run_001")
    assert res.status == "completed"
    assert res.source_fps == 10.0
    assert res.stride == 2
    assert res.frames_read == 30
    assert res.frames_processed == 15


def test_runner_event_time_timestamps(tmp_path: Path):
    """Event time = base_time_utc + frame_index / src_fps, never wall clock."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="time_clip", duration_sec=1.0, fps=10.0)
    runner, pipeline, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="time_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=5.0,
        source_note="test",
    )

    # Supply a detection on every frame
    tracker = ScriptedTracker(
        script={
            0: [TrackedDetection(local_track_id=1, bbox=BoundingBox2D(100.0, 100.0, 120.0, 140.0), confidence=0.9)],
            1: [TrackedDetection(local_track_id=1, bbox=BoundingBox2D(100.0, 100.0, 120.0, 140.0), confidence=0.9)],
            2: [TrackedDetection(local_track_id=1, bbox=BoundingBox2D(100.0, 100.0, 120.0, 140.0), confidence=0.9)],
        }
    )

    captured_obs = []
    orig_process = pipeline.process_observation

    def capture_process(obs):
        captured_obs.append(obs)
        return orig_process(obs)

    pipeline.process_observation = capture_process

    runner.run(config=config, tracker=tracker, run_id="run_time")

    assert len(captured_obs) >= 3
    # Frame 0 (t = 0.0s)
    assert captured_obs[0].timestamp == "2026-01-20T09:30:00Z"
    # Frame 2 (t = 2 / 10 = 0.2s)
    assert captured_obs[1].timestamp == "2026-01-20T09:30:00.200000Z"
    # Frame 4 (t = 4 / 10 = 0.4s)
    assert captured_obs[2].timestamp == "2026-01-20T09:30:00.400000Z"


def test_runner_bbox_clamping_and_scaling(tmp_path: Path):
    """Clamps bbox to 320x240 frame BEFORE scaling to camera 1920x1080 (exact numbers)."""
    video_path, _ = generate_synthetic_video(
        tmp_path, stem="scale_clip", duration_sec=0.2, fps=10.0, width=320, height=240
    )
    runner, pipeline, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="scale_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=10.0,
        source_note="test",
    )

    # Det 1: within bounds (10, 20, 50, 100) -> scale x by 6.0, y by 4.5
    # Det 2: exceeds frame bounds (0, 0, 400, 300) -> clamped to (0, 0, 320, 240) -> scaled to (0, 0, 1920, 1080)
    tracker = ScriptedTracker(
        script={
            0: [
                TrackedDetection(local_track_id=1, bbox=BoundingBox2D(10.0, 20.0, 50.0, 100.0), confidence=0.9),
                TrackedDetection(local_track_id=2, bbox=BoundingBox2D(0.0, 0.0, 400.0, 300.0), confidence=0.95),
            ]
        }
    )

    captured_obs = []
    orig_process = pipeline.process_observation

    def capture_process(obs):
        captured_obs.append(obs)
        return orig_process(obs)

    pipeline.process_observation = capture_process

    runner.run(config=config, tracker=tracker, run_id="run_scale")

    assert len(captured_obs) == 2
    b1 = captured_obs[0].bbox
    assert b1.u_min == pytest.approx(60.0)
    assert b1.v_min == pytest.approx(90.0)
    assert b1.u_max == pytest.approx(300.0)
    assert b1.v_max == pytest.approx(450.0)

    b2 = captured_obs[1].bbox
    assert b2.u_min == pytest.approx(0.0)
    assert b2.v_min == pytest.approx(0.0)
    assert b2.u_max == pytest.approx(1920.0)
    assert b2.v_max == pytest.approx(1080.0)


def test_runner_track_id_namespacing(tmp_path: Path):
    """Track ID format: {config.name}:{run_id}:{local_track_id} (full run_id, no truncation)."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="ns_clip", duration_sec=0.2, fps=10.0)
    runner, pipeline, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="ns_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )
    tracker = ScriptedTracker(
        script={
            0: [TrackedDetection(local_track_id=42, bbox=BoundingBox2D(10.0, 20.0, 30.0, 40.0), confidence=0.9)]
        }
    )

    captured_obs = []
    pipeline.process_observation = lambda obs: captured_obs.append(obs) or MagicMock(emitted_events=[], raised_incidents=[])

    runner.run(config=config, tracker=tracker, run_id="12345678abcdef")

    assert len(captured_obs) == 1
    assert captured_obs[0].track_id == "ns_clip:12345678abcdef:42"
    assert captured_obs[0].person_id is None


def test_runner_observations_reach_twin(tmp_path: Path):
    """Observations reach TwinStore event ledger."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="twin_clip", duration_sec=0.5, fps=10.0)
    runner, _, twin_engine = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="twin_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )
    tracker = ScriptedTracker(
        script={
            0: [TrackedDetection(local_track_id=1, bbox=BoundingBox2D(100.0, 50.0, 200.0, 100.0), confidence=0.9)]
        }
    )

    res = runner.run(config=config, tracker=tracker, run_id="run_twin")
    assert res.observations > 0
    assert res.events_emitted > 0

    events, total = twin_engine.store.get_events(limit=10)
    assert total > 0
    event_types = [e.event_type for e in events]
    assert "PERSON_DETECTED" in event_types


def test_runner_server_room_incident_generation(tmp_path: Path):
    """A track entering server-room camera produces UNAUTHORIZED_ENTRY and ACCESS_WITHOUT_BADGE."""
    video_path, _ = generate_synthetic_video(
        tmp_path, stem="srv_clip", duration_sec=0.2, fps=10.0, width=320, height=240
    )
    runner, _, twin_engine = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="srv_clip",
        video_file=video_path.name,
        camera_id="cam_server_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )

    # Frame bottom center (160, 120) scales to (960, 540) on 1920x1080, which projects to (34.98, 4.98) in server room
    tracker = ScriptedTracker(
        script={
            0: [TrackedDetection(local_track_id=1, bbox=BoundingBox2D(140.0, 80.0, 180.0, 120.0), confidence=0.95)]
        }
    )

    res = runner.run(config=config, tracker=tracker, run_id="run_srv")
    assert res.status == "completed"

    incidents = twin_engine.store.get_incidents()
    inc_types = {i.incident_type for i in incidents}
    assert "UNAUTHORIZED_ENTRY" in inc_types
    assert "ACCESS_WITHOUT_BADGE" in inc_types


def test_runner_max_frames(tmp_path: Path):
    """Runner stops when max_frames frames have been read."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="max_clip", duration_sec=3.0, fps=10.0)
    runner, _, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="max_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        max_frames=10,
        source_note="test",
    )
    tracker = ScriptedTracker({})

    res = runner.run(config=config, tracker=tracker, run_id="run_max")
    assert res.frames_read == 10


def test_runner_cancel(tmp_path: Path):
    """Setting cancel_event stops the run with status='cancelled'."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="cancel_clip", duration_sec=2.0, fps=10.0)
    runner, _, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="cancel_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )
    tracker = ScriptedTracker({})
    cancel_evt = threading.Event()
    cancel_evt.set()

    res = runner.run(config=config, tracker=tracker, run_id="run_cancel", cancel_event=cancel_evt)
    assert res.status == "cancelled"


def test_runner_unopenable_file(tmp_path: Path):
    """Non-existent video file raises FileNotFoundError."""
    runner, _, _ = _setup_runner_and_twin(tmp_path)
    config = ClipConfig(
        name="missing_clip",
        video_file="does_not_exist.mp4",
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )
    tracker = ScriptedTracker({})

    with pytest.raises(FileNotFoundError):
        runner.run(config=config, tracker=tracker, run_id="run_missing")


def test_runner_fps_fallback_warning(tmp_path: Path):
    """Invalid video FPS defaults to 25.0 with warning."""
    import cv2
    video_path, _ = generate_synthetic_video(tmp_path, stem="fps_clip", duration_sec=0.2, fps=10.0)
    runner, _, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="fps_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )
    tracker = ScriptedTracker({})

    with patch.object(cv2.VideoCapture, "get", return_value=0.0):
        res = runner.run(config=config, tracker=tracker, run_id="run_fps")
        assert res.source_fps == 25.0
        assert any("defaulted to 25.0" in w for w in res.warnings)


def test_runner_source_fps_override(tmp_path: Path):
    """Config source_fps_override is respected with warning."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="override_clip", duration_sec=0.2, fps=10.0)
    runner, _, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="override_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
        source_fps_override=25.0,
    )
    tracker = ScriptedTracker({})

    res = runner.run(config=config, tracker=tracker, run_id="run_ovr")
    assert res.source_fps == 25.0
    assert any("source fps overridden: container reported 10.0" in w for w in res.warnings)


def test_runner_fps_greater_than_120_fallback(tmp_path: Path):
    """Reported FPS > 120 defaults to 25.0 with warning."""
    import cv2
    video_path, _ = generate_synthetic_video(tmp_path, stem="bogus_fps_clip", duration_sec=0.2, fps=10.0)
    runner, _, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="bogus_fps_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        source_note="test",
    )
    tracker = ScriptedTracker({})

    with patch.object(cv2.VideoCapture, "get", return_value=1000.0):
        res = runner.run(config=config, tracker=tracker, run_id="run_bogus")
        assert res.source_fps == 25.0
        assert any("defaulted to 25.0" in w for w in res.warnings)



def test_runner_failing_observation_continues(tmp_path: Path):
    """One failing observation is recorded in errors and run continues."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="err_clip", duration_sec=0.4, fps=10.0)
    runner, pipeline, _ = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="err_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=10.0,
        source_note="test",
    )

    tracker = ScriptedTracker(
        script={
            0: [TrackedDetection(local_track_id=1, bbox=BoundingBox2D(10.0, 10.0, 20.0, 20.0), confidence=0.9)],
            1: [TrackedDetection(local_track_id=2, bbox=BoundingBox2D(10.0, 10.0, 20.0, 20.0), confidence=0.9)],
        }
    )

    call_count = 0
    orig_process = pipeline.process_observation

    def faulty_process(obs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Synthetic observation failure")
        return orig_process(obs)

    pipeline.process_observation = faulty_process

    res = runner.run(config=config, tracker=tracker, run_id="run_err")
    assert res.status == "completed"
    assert len(res.errors) == 1
    assert "Synthetic observation failure" in res.errors[0]
    assert res.observations == 1


def test_two_job_runs_of_same_clip_produce_disjoint_track_ids(tmp_path: Path):
    """Consecutive runs of one clip must never merge people: local track 1 of run A != of run B.

    Regression: run_id used to be a truncated time-based UUIDv7, so back-to-back runs shared the
    track-ID prefix and the twin merged different people into one track.
    """
    from src.vision.clip_jobs import ClipJobManager

    video_path, _ = generate_synthetic_video(tmp_path, stem="dup_clip", duration_sec=0.4, fps=10.0)
    runner, _, twin_engine = _setup_runner_and_twin(tmp_path)
    manager = ClipJobManager(runner=runner)
    config = ClipConfig(
        name="dup_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=10.0,
        source_note="test",
    )

    def factory():
        det = TrackedDetection(local_track_id=1, bbox=BoundingBox2D(800.0, 400.0, 900.0, 700.0), confidence=0.9)
        return ScriptedTracker({0: [det], 1: [det]})

    track_sets = []
    run_ids = []
    for _ in range(2):
        run_id = manager.start(config=config, tracker_factory=factory)
        thread = manager.get_thread(run_id)
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert manager.get(run_id)["status"] == "completed"
        run_ids.append(run_id)
        events, _ = twin_engine.store.get_events(limit=500)
        track_sets.append({e.track_id for e in events if run_id in e.track_id})

    assert run_ids[0] != run_ids[1]
    assert all(len(r) == len("run_") + 12 for r in run_ids)
    assert track_sets[0] and track_sets[1]
    assert track_sets[0].isdisjoint(track_sets[1])


def test_runner_event_time_track_exit(tmp_path: Path):
    """Track present in frames 0-4 then absent receives TRACK_LOST when absence > track_exit_seconds, dropping occupancy."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="exit_clip", duration_sec=3.0, fps=10.0)
    runner, _, twin_engine = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="exit_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=10.0,
        track_exit_seconds=1.0,
        source_note="test",
    )

    det = TrackedDetection(local_track_id=1, bbox=BoundingBox2D(800.0, 400.0, 900.0, 700.0), confidence=0.9)
    tracker = ScriptedTracker({0: [det], 1: [det], 2: [det], 3: [det], 4: [det]})

    res = runner.run(config=config, tracker=tracker, run_id="run_exit")
    assert res.status == "completed"
    assert res.event_type_counts.get("TRACK_LOST") == 1
    assert twin_engine.state.get_zone_occupancy("zone_reception") == 0


def test_runner_cleans_active_tracks_on_completion(tmp_path: Path):
    """After a completed run, twin_engine.state has no active tracks from that run."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="clean_clip", duration_sec=0.5, fps=10.0)
    runner, _, twin_engine = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="clean_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=10.0,
        track_exit_seconds=2.0,
        source_note="test",
    )

    det = TrackedDetection(local_track_id=1, bbox=BoundingBox2D(800.0, 400.0, 900.0, 700.0), confidence=0.9)
    # Track present in all frames up to end of video
    tracker = ScriptedTracker({i: [det] for i in range(5)})

    res = runner.run(config=config, tracker=tracker, run_id="run_clean")
    assert res.status == "completed"
    assert res.event_type_counts.get("TRACK_LOST") == 1
    assert len(twin_engine.state.get_active_tracks()) == 0


def test_runner_zone_over_capacity_auto_resolves(tmp_path: Path):
    """Scripted crowd exceeding max_occupancy then leaving causes ZONE_OVER_CAPACITY to auto-resolve."""
    video_path, _ = generate_synthetic_video(tmp_path, stem="crowd_clip", duration_sec=2.0, fps=10.0)
    runner, _, twin_engine = _setup_runner_and_twin(tmp_path)

    config = ClipConfig(
        name="crowd_clip",
        video_file=video_path.name,
        camera_id="cam_reception_01",
        base_time_utc="2026-01-20T09:30:00Z",
        process_fps=10.0,
        track_exit_seconds=0.5,
        source_note="test",
    )

    # Reception max_occupancy is 15; create 16 distinct tracks in frame 0
    crowd = [
        TrackedDetection(local_track_id=i, bbox=BoundingBox2D(800.0, 400.0, 900.0, 700.0), confidence=0.9)
        for i in range(1, 17)
    ]
    # Frame 0 has crowd; frames 1..19 have nobody (crowd leaves)
    tracker = ScriptedTracker({0: crowd})

    res = runner.run(config=config, tracker=tracker, run_id="run_crowd")
    assert res.status == "completed"
    assert res.event_type_counts.get("TRACK_LOST") == 16

    all_incidents = twin_engine.store.get_incidents(zone_id="zone_reception")
    incidents = [i for i in all_incidents if i.incident_type == "ZONE_OVER_CAPACITY"]
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.status == "RESOLVED"
    assert inc.end_time is not None

