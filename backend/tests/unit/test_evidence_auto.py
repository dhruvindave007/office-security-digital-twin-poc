"""Evidence clips around incidents (pre/post-roll, shared bursts, quota) and the auto-investigator
(severity filter, duplicate cooldown, hourly cap, AWS sign-in pause, waits for evidence)."""
from datetime import datetime, timedelta, timezone
import json
import time

import numpy as np

from src.ai.errors import BedrockCredentialsError
from src.ai.investigation.auto import AutoInvestigator
from src.live.evidence import EvidenceRecorder
from src.twin.models import Incident

T0 = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _inc(iid, t, track="live:cam_3:4", severity="HIGH", itype="TAILGATING", zone="zone_lab"):
    return Incident(incident_id=iid, incident_type=itype, severity=severity, zone_id=zone, track_id=track,
                    start_time=(T0 + timedelta(seconds=t)).isoformat().replace("+00:00", "Z"))


def _feed(rec, cam, t_from, t_to, fps=5):
    frame = np.zeros((120, 160, 3), np.uint8)
    for i in range(int(t_from * fps), int(t_to * fps)):
        rec.add_frame(cam, T0 + timedelta(seconds=i / fps), frame, [{"id": 4, "conf": 0.9, "box": [10, 10, 30, 60]}])


def _wait(cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_clip_has_pre_and_post_roll_and_manifest(tmp_path):
    rec = EvidenceRecorder(str(tmp_path), pre_seconds=4, post_seconds=2, camera_for_incident=lambda i: ["cam_3"])
    _feed(rec, "cam_3", 0, 10)
    rec.on_incidents([_inc("i1", 8)], [])           # raised now (t=10) about a moment at t=8 (late decision)
    _feed(rec, "cam_3", 10, 14)
    assert _wait(lambda: rec.clips_written == 1)
    m = rec.manifest("i1")
    c = m["clips"][0]
    assert c["camera_id"] == "cam_3" and (tmp_path / c["video"]).stat().st_size > 0 and (tmp_path / c["key_frame"]).exists()
    assert 3.5 <= c["incident_offset_s"] <= 4.1        # 4 s pre-roll before the incident moment
    assert 29 <= c["frames"] <= 32                      # t=4..10.2 at 5 fps
    assert rec.wait_ready("i1", 1)
    rec.close()


def test_burst_of_incidents_shares_one_clip(tmp_path):
    rec = EvidenceRecorder(str(tmp_path), pre_seconds=2, post_seconds=2, max_clip_seconds=20, camera_for_incident=lambda i: ["cam_3"])
    _feed(rec, "cam_3", 0, 5)
    rec.on_incidents([_inc("a", 5)], [])
    _feed(rec, "cam_3", 5, 6)
    rec.on_incidents([_inc("b", 6)], [])
    _feed(rec, "cam_3", 6, 12)
    assert _wait(lambda: rec.clips_written == 1)
    time.sleep(0.3)
    assert rec.clips_written == 1
    assert rec.manifest("a")["clips"][0]["video"] == rec.manifest("b")["clips"][0]["video"]
    rec.close()


def test_low_severity_and_unmapped_incidents_record_nothing(tmp_path):
    rec = EvidenceRecorder(str(tmp_path), camera_for_incident=lambda i: [])
    _feed(rec, "cam_3", 0, 3)
    rec.on_incidents([_inc("low", 2, severity="LOW"), _inc("nocam", 2)], [])
    assert rec.wait_ready("nocam", 1)                   # nothing to wait for
    rec.close()
    assert rec.manifest("low") is None and rec.manifest("nocam") is None


class FakeService:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def investigate(self, incident_id, question):
        self.calls.append(incident_id)
        if self.fail:
            raise self.fail
        return {"status": "COMPLETED", "verification": {"passed": True}, "cost_usd": 0.002}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _drain(auto, n):
    assert _wait(lambda: sum(auto.counts.values()) >= n, 5)


def test_auto_investigates_serious_once_per_type_and_room():
    svc, clock = FakeService(), Clock()
    auto = AutoInvestigator(svc, cooldown_seconds=600, max_per_hour=10, clock=clock)
    auto.on_incidents([_inc("1", 0), _inc("2", 1), _inc("3", 2, itype="ACCESS_WITHOUT_BADGE"),
                       _inc("4", 3, severity="MEDIUM")], [])
    _drain(auto, 3)
    assert svc.calls == ["1", "3"]                      # 2 = duplicate within cooldown; 4 = below HIGH
    assert auto.counts == {"completed": 2, "skipped_duplicate_cooldown": 1}
    clock.t += 601
    auto.on_incidents([_inc("5", 700)], [])
    _drain(auto, 4)
    assert svc.calls[-1] == "5"
    auto.close()


def test_auto_hourly_cap():
    svc = FakeService()
    auto = AutoInvestigator(svc, cooldown_seconds=0, max_per_hour=2, clock=Clock())
    auto.on_incidents([_inc(str(i), i) for i in range(4)], [])
    _drain(auto, 4)
    assert len(svc.calls) == 2 and auto.counts["skipped_hourly_cap"] == 2
    auto.close()


def test_auto_pauses_when_aws_sign_in_expired():
    svc, clock = FakeService(fail=BedrockCredentialsError("token expired")), Clock()
    auto = AutoInvestigator(svc, cooldown_seconds=0, pause_on_credentials_seconds=600, clock=clock)
    auto.on_incidents([_inc("1", 0), _inc("2", 1, itype="ACCESS_WITHOUT_BADGE")], [])
    _drain(auto, 2)
    assert svc.calls == ["1"] and auto.counts == {"failed_no_aws_sign_in": 1, "skipped_paused_no_aws_sign_in": 1}
    assert auto.status()["paused_seconds_left"] == 600
    auto.close()


def test_auto_waits_for_evidence_before_investigating():
    order = []
    svc = FakeService()
    svc.investigate = lambda iid, q: order.append(("investigate", iid)) or {"status": "COMPLETED", "verification": {}}
    auto = AutoInvestigator(svc, wait_for_evidence=lambda iid, t: order.append(("evidence", iid)) or True, clock=Clock())
    auto.on_incidents([_inc("1", 0)], [])
    _drain(auto, 1)
    assert order == [("evidence", "1"), ("investigate", "1")]
    auto.close()
