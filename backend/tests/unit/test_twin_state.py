# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import concurrent.futures
from datetime import datetime, timedelta
import threading
from src.twin.state import TwinState


def test_twin_state_track_lifecycle():
    """Verify track insertion, spatial movement, zone occupancy, and dwell timing."""
    state = TwinState()
    now_iso = "2026-09-19T12:00:00Z"

    # 1. New track enters reception
    old_z, new_z = state.update_track(
        track_id="trk_1",
        camera_id="cam_1",
        zone_id="zone_reception",
        position=(5.0, 5.0),
        timestamp=now_iso,
    )
    assert old_z is None
    assert new_z == "zone_reception"
    assert state.get_zone_occupancy("zone_reception") == 1

    # 2. Track transitions from reception to hallway
    later_iso = "2026-09-19T12:01:00Z"
    old_z2, new_z2 = state.update_track(
        track_id="trk_1",
        camera_id="cam_2",
        zone_id="zone_hallway",
        position=(15.0, 5.0),
        timestamp=later_iso,
    )
    assert old_z2 == "zone_reception"
    assert new_z2 == "zone_hallway"
    assert state.get_zone_occupancy("zone_reception") == 0
    assert state.get_zone_occupancy("zone_hallway") == 1

    # 3. Track moves within same zone
    still_later_iso = "2026-09-19T12:02:00Z"
    old_z3, new_z3 = state.update_track(
        track_id="trk_1",
        camera_id="cam_2",
        zone_id="zone_hallway",
        position=(16.0, 5.0),
        timestamp=still_later_iso,
    )
    assert old_z3 == "zone_hallway"
    assert new_z3 == "zone_hallway"
    assert state.get_zone_occupancy("zone_hallway") == 1


def test_twin_state_ttl_pruning():
    """Verify pruning removes inactive tracks exceeding TTL threshold."""
    state = TwinState()
    t0 = datetime.fromisoformat("2026-09-19T12:00:00+00:00")

    # Track A active at T0
    state.update_track("trk_A", "cam_1", "zone_reception", (1.0, 1.0), t0.isoformat())

    # Track B active at T0 + 25s
    t_b = t0 + timedelta(seconds=25)
    state.update_track("trk_B", "cam_1", "zone_reception", (2.0, 2.0), t_b.isoformat())

    # Prune at T0 + 35s with TTL = 30s
    prune_time = t0 + timedelta(seconds=35)
    pruned = state.prune_inactive_tracks(prune_time.isoformat(), ttl_seconds=30.0)

    # Track A is 35s old (> 30s) -> pruned
    # Track B is 10s old (<= 30s) -> remains active
    assert len(pruned) == 1
    assert pruned[0].track_id == "trk_A"
    assert state.get_track("trk_A") is None
    assert state.get_track("trk_B") is not None
    assert state.get_zone_occupancy("zone_reception") == 1


def test_twin_state_thread_safety():
    """Verify thread safety under heavy concurrent track updates."""
    state = TwinState()
    num_threads = 8
    updates_per_thread = 100

    def worker(thread_idx: int):
        for i in range(updates_per_thread):
            track_id = f"trk_{thread_idx}_{i % 5}"
            zone_id = f"zone_{i % 3}"
            ts = datetime.utcnow().isoformat()
            state.update_track(track_id, "cam_1", zone_id, (float(i), float(i)), ts)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, idx) for idx in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify no race condition or deadlock: total active tracks is exactly num_threads * 5
    tracks = state.get_active_tracks()
    assert len(tracks) == num_threads * 5

    # Verify sum of zone occupancies equals total active tracks
    total_occ = sum(state.get_all_zone_occupancies().values())
    assert total_occ == len(tracks)


def test_twin_state_timezone_mixed_pruning():
    """Verify pruning with mixed timezone-aware and naive timestamps does not raise."""
    state = TwinState()

    # Case 1: aware last_seen, naive now_iso
    aware_ts = "2026-09-19T12:00:00+00:00"
    state.update_track("trk_aware", "cam_1", "zone_reception", (1.0, 1.0), aware_ts)

    naive_now = "2026-09-19T12:01:00"  # 60s later, naive
    pruned_1 = state.prune_inactive_tracks(naive_now, ttl_seconds=30.0)
    assert len(pruned_1) == 1
    assert pruned_1[0].track_id == "trk_aware"
    assert state.get_track("trk_aware") is None

    # Case 2: naive last_seen, aware now_iso
    naive_ts = "2026-09-19T12:00:00"
    state.update_track("trk_naive", "cam_1", "zone_reception", (1.0, 1.0), naive_ts)

    aware_now = "2026-09-19T12:01:00+00:00"  # 60s later, aware
    pruned_2 = state.prune_inactive_tracks(aware_now, ttl_seconds=30.0)
    assert len(pruned_2) == 1
    assert pruned_2[0].track_id == "trk_naive"
    assert state.get_track("trk_naive") is None


def test_twin_engine_prune_tracks_default_and_custom_ttl(tmp_path):
    """Verify TwinEngine.prune_tracks uses engine.track_ttl_seconds when ttl_seconds=None, and overrides when set."""
    from src.twin.bootstrap import TwinBootstrapService

    db_path = str(tmp_path / "test_prune_engine.db")
    engine = TwinBootstrapService.bootstrap(
        db_path=db_path,
        seed_path="src/twin/floorplan_seed.yaml",
        track_ttl_seconds=10.0,
    )
    t0 = "2026-09-19T12:00:00Z"
    engine.state.update_track("trk_1", "cam_reception_01", "zone_reception", (1.0, 1.0), t0)

    # 5 seconds later with default TTL (10.0) -> not pruned
    lost = engine.prune_tracks(now_iso="2026-09-19T12:00:05Z", ttl_seconds=None)
    assert len(lost) == 0
    assert engine.state.get_track("trk_1") is not None

    # 15 seconds later with default TTL (10.0) -> pruned
    lost2 = engine.prune_tracks(now_iso="2026-09-19T12:00:15Z", ttl_seconds=None)
    assert len(lost2) == 1
    assert lost2[0].track_id == "trk_1"
    assert lost2[0].event_type == "TRACK_LOST"
    assert engine.state.get_track("trk_1") is None

    # Verify custom TTL parameter
    engine.state.update_track("trk_2", "cam_reception_01", "zone_reception", (1.0, 1.0), "2026-09-19T12:00:20Z")
    # 2 seconds later with custom ttl_seconds=1.0 -> pruned early
    lost3 = engine.prune_tracks(now_iso="2026-09-19T12:00:22Z", ttl_seconds=1.0)
    assert len(lost3) == 1
    assert lost3[0].track_id == "trk_2"
    assert engine.state.get_track("trk_2") is None

