# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from src.twin.engine import DigitalTwinEngine
from src.twin.geometry import GeometryEngine
from src.twin.models import Zone
from src.twin.snapshot import SnapshotManager
from src.twin.state import TwinState
from src.twin.store import TwinStore


def test_deterministic_replay_and_snapshot_delta():
    """Verify that Replay from 0 equals Snapshot + Delta Replay equals Live State.

    Guarantees 100% deterministic state reconstruction from the immutable event ledger.
    """
    store = TwinStore(db_path=":memory:")

    # Setup 2 zones
    z1 = Zone(zone_id="zone_A", name="Zone A", zone_type="workspace", security_level=1)
    z2 = Zone(zone_id="zone_B", name="Zone B", zone_type="secure", security_level=3)
    store.insert_zone(z1)
    store.insert_zone(z2)

    from src.twin.models import Camera
    store.insert_camera(Camera(camera_id="cam_A", name="Camera A", primary_zone_id="zone_A"))
    store.insert_camera(Camera(camera_id="cam_B", name="Camera B", primary_zone_id="zone_B"))

    vertices = {
        "zone_A": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        "zone_B": [(10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0)],
    }
    geom = GeometryEngine([z1, z2], vertices)

    # Snapshot every 10 events
    snap_mgr = SnapshotManager(interval_events=10)
    live_state = TwinState()
    engine = DigitalTwinEngine(
        store=store,
        state=live_state,
        geometry=geom,
        snapshot_manager=snap_mgr,
    )

    # Ingest 25 observations across multiple tracks
    for step in range(25):
        ts = f"2026-09-19T10:{step:02d}:00Z"
        if step % 2 == 0:
            # Track 1 enters and moves in Zone A
            engine.ingest_observation("trk_1", "cam_A", 5.0, 5.0, ts)
        elif step % 3 == 0:
            # Track 1 transitions to Zone B
            engine.ingest_observation("trk_1", "cam_B", 15.0, 5.0, ts)
        else:
            # Track 2 enters Zone B
            engine.ingest_observation("trk_2", "cam_B", 12.0, 4.0, ts)

    # Record the live state
    live_state_dict = live_state.to_dict()
    assert len(live_state_dict["active_tracks"]) > 0

    # -------------------------------------------------------------
    # 1. Full replay from sequence 0
    # -------------------------------------------------------------
    all_events = store.get_events_since(0)
    assert len(all_events) > 0

    full_replay_state = TwinState()
    full_replay_engine = DigitalTwinEngine(
        store=store,
        state=full_replay_state,
        geometry=geom,
    )
    full_replay_engine.replay_events(all_events)
    full_replay_dict = full_replay_state.to_dict()

    # Assert full replay equals live state exactly
    assert full_replay_dict == live_state_dict

    # -------------------------------------------------------------
    # 2. Snapshot checkpoint + delta replay
    # -------------------------------------------------------------
    delta_replay_state = TwinState()
    snap_seq = snap_mgr.restore_latest(delta_replay_state, store=store)
    assert snap_seq is not None
    assert snap_seq > 0

    # Fetch remaining delta events since snapshot
    delta_events = store.get_events_since(snap_seq)
    delta_replay_engine = DigitalTwinEngine(
        store=store,
        state=delta_replay_state,
        geometry=geom,
    )
    delta_replay_engine.replay_events(delta_events)
    delta_replay_dict = delta_replay_state.to_dict()

    # Assert Snapshot + Delta Replay equals Live State exactly
    assert delta_replay_dict == live_state_dict
    assert delta_replay_dict == full_replay_dict
