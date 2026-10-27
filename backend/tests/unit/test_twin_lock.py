# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import threading
from typing import List

import pytest

from src.access.config import load_access_config
from src.rules.correlation import CorrelationEngine
from src.rules.engine import IncidentRuleEngine
from src.rules.policy import load_access_policy
from src.twin.bootstrap import TwinBootstrapService
from src.vision.geometry import CameraObservation, PixelPoint
from src.vision.pipeline import ObservationPipeline
from src.vision.seed import VisionSeedLoader
from src.vision.zones import VisionZoneResolver


def test_twin_write_lock_concurrency(tmp_path: Path):
    """Stress test TWIN_WRITE_LOCK with 4 concurrent threads x 50 observations.

    Verifies thread safety:
    - No exceptions raised during concurrent execution.
    - Total event count in TwinStore matches the sum of events returned by ObservationPipeline.
    - Event sequence numbers are strictly unique and contiguous (1..N).
    """
    db_path = str(tmp_path / "test_twin_lock.db")
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

    num_threads = 4
    obs_per_thread = 50

    results_per_thread: List[List] = [[] for _ in range(num_threads)]
    thread_errors: List[Exception] = []
    error_lock = threading.Lock()

    def worker(thread_idx: int):
        try:
            for j in range(obs_per_thread):
                ts = f"2026-01-20T09:{thread_idx:02d}:{j:02d}Z"
                obs = CameraObservation(
                    camera_id="cam_reception_01",
                    track_id=f"track_thread_{thread_idx}",
                    bottom_center=PixelPoint(u=960.0, v=540.0),
                    confidence=0.9,
                    timestamp=ts,
                )
                res = pipeline.process_observation(obs)
                results_per_thread[thread_idx].append(res)
        except Exception as exc:
            with error_lock:
                thread_errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i,), name=f"lock-test-worker-{i}")
        for i in range(num_threads)
    ]

    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=15.0)
        assert not t.is_alive(), f"Thread {t.name} timed out"

    # 1. Assert no exceptions occurred in any worker thread
    assert thread_errors == [], f"Thread errors encountered: {thread_errors}"

    # 2. Count total events returned by pipeline calls across all threads
    total_pipeline_emitted_events = sum(
        len(res.emitted_events)
        for thread_results in results_per_thread
        for res in thread_results
    )
    assert total_pipeline_emitted_events > 0

    # 3. Assert event count in TwinStore equals the sum returned by pipeline
    events, total_store_events = twin_engine.store.get_events(limit=total_pipeline_emitted_events + 50)
    assert total_store_events == total_pipeline_emitted_events
    assert len(events) == total_pipeline_emitted_events

    # 4. Assert sequence_num values are unique and contiguous
    seq_nums = [e.sequence_num for e in events]
    assert len(seq_nums) == len(set(seq_nums)), "Sequence numbers contain duplicates"
    assert sorted(seq_nums) == list(range(1, total_pipeline_emitted_events + 1)), (
        "Sequence numbers are not contiguous starting from 1"
    )
