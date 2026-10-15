"""Live camera pipeline with fake streams and a fake detector: newest-frame semantics, reconnect,
twin feed with wall-clock times, exits on stop, entry-gate wiring, API, and credential redaction."""
import os
import tempfile
import threading
import time

import numpy as np
import pytest
import yaml

from src.live.manager import LiveManager
from src.live.source import FrameSource, _redact
from src.rules.engine import IncidentRuleEngine
from src.rules.policy import load_access_policy
from src.twin.bootstrap import TwinBootstrapService
from src.vision.geometry import BoundingBox2D
from src.vision.tracking.base import TrackedDetection

SITE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "sites", "wisenet")


class FakeCap:
    """A camera that yields numbered frames; `fail_after` simulates a dropped network stream."""
    def __init__(self, n=10_000, fail_after=None, fps=50.0):
        self.i, self.n, self.fail_after, self.fps = 0, n, fail_after, fps

    def isOpened(self):
        return True

    def get(self, prop):
        return self.fps

    def set(self, *a):
        return True

    def read(self):
        if self.fail_after is not None and self.i >= self.fail_after:
            return False, None
        if self.i >= self.n:
            return False, None
        self.i += 1
        time.sleep(0.005)
        f = np.zeros((480, 640, 3), np.uint8)
        f[0, 0, 0] = self.i % 256
        return True, f

    def release(self):
        pass


class OnePersonTracker:
    """Always sees one person standing in the corridor (cam_2's metric range)."""
    def update(self, frame):
        return [TrackedDetection(local_track_id=7, bbox=BoundingBox2D(u_min=200, v_min=100, u_max=240, v_max=368), confidence=0.9)]

    def reset(self):
        pass


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_source_keeps_only_the_newest_frame():
    src = FrameSource("c", "rtsp://cam/stream", open_fn=lambda u: FakeCap())
    src.start()
    assert _wait(lambda: src.frames_read > 20)
    _, _, seq1 = src.latest(0)
    time.sleep(0.1)
    _, _, seq2 = src.latest(0)
    src.stop()
    assert seq2 > seq1 + 3                     # frames in between were dropped, not queued
    assert src.latest(newer_than=seq2 + 10**6) is None


def test_source_reconnects_after_stream_failure():
    opens = []
    src = FrameSource("c", "rtsp://cam/stream", max_backoff_s=0.05,
                      open_fn=lambda u: (opens.append(1), FakeCap(fail_after=5))[1])
    src.start()
    assert _wait(lambda: src.reconnects >= 2 and src.frames_read >= 10)
    src.stop()
    assert len(opens) >= 2 and src.state == "stopped"   # opened again after each failure


def test_redact_hides_camera_password():
    assert _redact("rtsp://admin:s3cret@10.0.0.5:554/ch1") == "rtsp://***@10.0.0.5:554/ch1"
    assert "s3cret" not in str(FrameSource("c", "rtsp://admin:s3cret@10.0.0.5/x").status())


@pytest.fixture
def site_runtime(tmp_path):
    eng = TwinBootstrapService.bootstrap(db_path=str(tmp_path / "live.db"), seed_path=os.path.join(SITE, "floorplan_seed.yaml"))
    rules = IncidentRuleEngine(eng.store, eng.state, load_access_policy(os.path.join(SITE, "access_policy.yaml")))
    cfg = {"process_fps": 20, "track_exit_seconds": 0.3, "calibration_path": os.path.abspath(os.path.join(SITE, "calibration.json")),
           "cameras": [{"camera_id": "cam_2", "url": "rtsp://fake/cam_2"},
                       {"camera_id": "cam_1", "url": "rtsp://fake/cam_1", "feed_twin": False}],
           "entry_gates": {"cam_3": {"roi": [219, 26, 94, 145], "image_size": [640, 480]}}}
    path = tmp_path / "live.yaml"
    path.write_text(yaml.safe_dump(cfg))

    class S:
        live_config_path = str(path)
    mgr = LiveManager(S(), eng, rules, tracker_builder=lambda cam: OnePersonTracker(),
                      source_factory=lambda c: FrameSource(c.camera_id, c.url, open_fn=lambda u: FakeCap()))
    yield eng, rules, mgr
    mgr.stop()
    eng.store.close()


def test_live_feeds_twin_with_wall_clock_and_releases_on_stop(site_runtime):
    eng, rules, mgr = site_runtime
    t0 = time.time()
    st = mgr.start()
    assert st["running"] and len(st["cameras"]) == 2
    assert _wait(lambda: "live:cam_2:7" in {t.track_id for t in eng.state.get_active_tracks()})
    assert "live:cam_1:7" not in {t.track_id for t in eng.state.get_active_tracks()}   # display-only camera
    track = next(t for t in eng.state.get_active_tracks() if t.track_id == "live:cam_2:7")
    from src.core.timeutil import parse_utc
    assert abs(parse_utc(track.last_seen).timestamp() - time.time()) < 5                 # wall-clock stamps
    assert track.current_zone_id == "zone_corridor"
    assert _wait(lambda: mgr.status()["cameras"][0]["frames_processed"] >= 5)
    s = mgr.status()["cameras"][0]
    assert s["latency_ms"]["end_to_end_ms"]["p50"] is not None and s["source"]["state"] == "live"
    assert mgr.jpeg("cam_2")[:2] == b"\xff\xd8"                                            # JPEG picture
    assert rules.correlation is None or rules.correlation.entry_filter is not None
    mgr.stop()
    assert "live:cam_2:7" not in {t.track_id for t in eng.state.get_active_tracks()}    # left on stop
    assert time.time() - t0 < 20


def test_live_refuses_uncalibrated_twin_camera(site_runtime, tmp_path):
    eng, rules, mgr = site_runtime
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"calibration_path": os.path.abspath(os.path.join(SITE, "calibration.json")),
                                   "cameras": [{"camera_id": "cam_99", "url": "rtsp://x"}]}))
    with pytest.raises(ValueError, match="cam_99"):
        mgr.start(str(bad))
    assert not mgr.running


def test_live_api_status_start_stop_frame(site_runtime):
    from fastapi.testclient import TestClient
    from src.main import app
    eng, rules, mgr = site_runtime
    with TestClient(app) as client:
        app.state.live_manager = mgr
        assert client.get("/api/live/status").json()["running"] is False
        assert client.post("/api/live/start", json={}).status_code == 200
        assert client.post("/api/live/start", json={}).status_code == 409          # already running
        assert _wait(lambda: mgr.jpeg("cam_2") is not None)
        r = client.get("/api/live/cameras/cam_2/frame.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert client.get("/api/live/cameras/nope/frame.jpg").status_code == 404
        assert client.post("/api/live/stop").json()["stopped"] == ["cam_1", "cam_2"]
