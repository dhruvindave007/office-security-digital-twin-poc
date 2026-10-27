# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import logging
from typing import Optional

from src.twin.engine import DigitalTwinEngine
from src.twin.seed import load_floorplan_seed
from src.twin.snapshot import SnapshotManager
from src.twin.state import TwinState
from src.twin.store import TwinStore
from src.twin.telemetry import TwinTelemetry

logger = logging.getLogger("office_vision.twin.bootstrap")


class TwinBootstrapService:
    """Startup initialization and state recovery service for the Digital Twin (Patch 6).

    Executes migrations, loads canonical floorplan seed data if unseeded, restores the latest
    valid snapshot checkpoint, and replays uncheckpointed delta ledger events to achieve
    100% deterministic state reconstruction.
    """

    @classmethod
    def bootstrap(
        cls,
        db_path: str = "data/digital_twin.db",
        seed_path: Optional[str] = None,
        track_ttl_seconds: float = 30.0,
        snapshot_interval: int = 100,
        position_sample_seconds: float = 0.0,
    ) -> DigitalTwinEngine:
        """Bootstrap the complete Digital Twin runtime."""
        logger.info("Initializing TwinStore at '%s'...", db_path)
        store = TwinStore(db_path=db_path)

        # Idempotent floorplan seed loading (loads exactly once)
        load_floorplan_seed(store=store, seed_path=seed_path)

        state = TwinState()
        snapshot_mgr = SnapshotManager(interval_events=snapshot_interval)
        telemetry = TwinTelemetry()

        # Step 1: Restore state from latest snapshot checkpoint if available
        last_seq = snapshot_mgr.restore_latest(state=state, store=store)
        start_seq = last_seq if last_seq is not None else 0

        # Step 2: Initialize engine (loads zones and builds STRtree spatial index)
        engine = DigitalTwinEngine(
            store=store,
            state=state,
            snapshot_manager=snapshot_mgr,
            telemetry=telemetry,
            track_ttl_seconds=track_ttl_seconds,
            position_sample_seconds=position_sample_seconds,
        )

        # Step 3: Replay delta events since snapshot checkpoint
        delta_events = store.get_events_since(start_seq)
        if delta_events:
            logger.info(
                "Replaying %d delta events (from sequence_num > %d) to reconcile active state...",
                len(delta_events),
                start_seq,
            )
            applied = engine.replay_events(delta_events)
            logger.info("Successfully applied %d delta events.", applied)

        logger.info(
            "Digital Twin bootstrap complete. Active tracks: %d.",
            len(state.get_active_tracks()),
        )
        return engine
