# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import pytest
from src.twin.snapshot import SnapshotManager
from src.twin.state import TwinState
from src.twin.store import TwinStore


def test_snapshot_checkpoint_and_restore():
    """Verify state serialization, SHA-256 checksum computation, and restoration."""
    store = TwinStore(db_path=":memory:")
    mgr = SnapshotManager(interval_events=10)
    state = TwinState()

    # Add active tracks
    state.update_track("trk_101", "cam_1", "zone_lobby", (2.0, 3.0), "2026-09-19T10:00:00Z")
    state.update_track("trk_102", "cam_2", "zone_server", (25.0, 5.0), "2026-09-19T10:00:05Z")

    assert mgr.should_snapshot(9) is False
    assert mgr.should_snapshot(10) is True

    # Create checkpoint
    snapshot = mgr.create_snapshot(state, sequence_num=10, store=store)
    assert snapshot.sequence_num == 10
    assert len(snapshot.checksum) == 64  # SHA-256 hex string

    # Restore into clean state instance
    fresh_state = TwinState()
    restored_seq = mgr.restore_latest(fresh_state, store=store)

    assert restored_seq == 10
    assert len(fresh_state.get_active_tracks()) == 2
    assert fresh_state.get_track("trk_101").current_zone_id == "zone_lobby"
    assert fresh_state.get_track("trk_102").current_zone_id == "zone_server"


def test_snapshot_checksum_tamper_detection():
    """Verify that tampered snapshot data in the database fails checksum validation."""
    store = TwinStore(db_path=":memory:")
    mgr = SnapshotManager(interval_events=5)
    state = TwinState()
    state.update_track("trk_1", "cam_1", "zone_1", (1.0, 1.0), "2026-09-19T10:00:00Z")

    snapshot = mgr.create_snapshot(state, sequence_num=5, store=store)

    # Tamper with state_json directly in SQLite
    with store.get_connection() as conn:
        conn.execute(
            "UPDATE snapshots SET state_json = ? WHERE snapshot_id = ?;",
            ('{"tampered": true}', snapshot.snapshot_id),
        )

    # Attempt restore -> must fail checksum verification
    tampered_state = TwinState()
    with pytest.raises(ValueError, match="checksum mismatch"):
        mgr.restore_latest(tampered_state, store=store)
