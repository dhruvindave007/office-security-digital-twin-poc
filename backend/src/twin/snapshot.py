# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime
import hashlib
import json
import logging
import time
from typing import Optional

from src.twin.models import TwinStateSnapshot
from src.twin.state import TwinState
from src.twin.store import TwinStore

logger = logging.getLogger("office_vision.twin.snapshot")


class SnapshotManager:
    """Manages periodic state checkpointing and verified state restoration (Patch 3).

    Periodically serializes the in-memory entity registry to the SQLite snapshots table
    with SHA-256 checksums, enabling fast recovery and bounded event replay.
    """

    def __init__(self, interval_events: int = 100):
        self.interval_events = interval_events
        self._last_snapshot_seq: int = 0

    @property
    def last_snapshot_seq(self) -> int:
        return self._last_snapshot_seq

    def should_snapshot(self, current_sequence_num: int) -> bool:
        """Evaluate if current sequence number has reached the snapshot checkpoint threshold."""
        if current_sequence_num <= 0:
            return False
        return (current_sequence_num - self._last_snapshot_seq) >= self.interval_events

    def create_snapshot(
        self,
        state: TwinState,
        sequence_num: int,
        store: TwinStore,
    ) -> TwinStateSnapshot:
        """Create, checksum, and persist a snapshot of the current in-memory state."""
        state_dict = state.to_dict()
        state_json = json.dumps(state_dict, sort_keys=True)
        # Patch 3: SHA-256 integrity checksum
        checksum = hashlib.sha256(state_json.encode("utf-8")).hexdigest()

        snapshot_id = f"snap_{sequence_num}_{int(time.time())}"
        snapshot = TwinStateSnapshot(
            snapshot_id=snapshot_id,
            sequence_num=sequence_num,
            timestamp=datetime.utcnow().isoformat(),
            state_json=state_json,
            checksum=checksum,
        )

        store.save_snapshot(snapshot)
        self._last_snapshot_seq = sequence_num
        logger.info("Created state snapshot '%s' at sequence_num=%d (checksum: %s)", snapshot_id, sequence_num, checksum[:8])
        return snapshot

    def restore_latest(self, state: TwinState, store: TwinStore) -> Optional[int]:
        """Restore in-memory state from the latest validated checkpoint in the store.

        Verifies SHA-256 checksum integrity prior to applying state to memory.

        Returns:
            The sequence_num of the restored snapshot, or None if no snapshot exists.
        Raises:
            ValueError: If snapshot checksum validation fails (data corruption detected).
        """
        snapshot = store.get_latest_snapshot()
        if not snapshot:
            logger.info("No prior snapshot found in database; starting with clean state.")
            return None

        # Verify integrity
        computed_checksum = hashlib.sha256(snapshot.state_json.encode("utf-8")).hexdigest()
        if computed_checksum != snapshot.checksum:
            raise ValueError(
                f"Snapshot '{snapshot.snapshot_id}' checksum mismatch! "
                f"Expected {snapshot.checksum}, computed {computed_checksum}. Checkpoint corrupted."
            )

        state_data = json.loads(snapshot.state_json)
        state.from_dict(state_data)
        self._last_snapshot_seq = snapshot.sequence_num

        logger.info(
            "Restored state snapshot '%s' at sequence_num=%d with %d active tracks.",
            snapshot.snapshot_id,
            snapshot.sequence_num,
            len(state_data.get("active_tracks", [])),
        )
        return snapshot.sequence_num
