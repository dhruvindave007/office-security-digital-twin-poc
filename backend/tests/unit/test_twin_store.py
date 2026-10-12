import sqlite3
import tempfile
from pathlib import Path
import pytest
from src.twin.models import TwinEvent, Zone
from src.twin.store import TwinStore


def test_twin_store_pragmas_and_wal():
    """Verify TwinStore sets WAL mode, foreign keys, synchronous=NORMAL, and busy_timeout=5000."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_twin.db")
        store = TwinStore(db_path=db_path)

        with store.get_connection() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]

            assert journal_mode.lower() == "wal"
            assert foreign_keys == 1
            assert busy_timeout == 5000
            # In SQLite, PRAGMA synchronous = NORMAL returns 1
            assert synchronous == 1

        store.close()


def test_append_only_event_ledger():
    """Verify append_event inserts rows with monotonic sequence numbers."""
    store = TwinStore(db_path=":memory:")

    evt1 = TwinEvent(
        event_id="evt_001",
        event_type="PERSON_DETECTED",
        timestamp="2026-09-19T10:00:00Z",
        track_id="trk_1",
    )
    evt2 = TwinEvent(
        event_id="evt_002",
        event_type="ZONE_ENTERED",
        timestamp="2026-09-19T10:00:01Z",
        track_id="trk_1",
        zone_id="zone_lobby",
    )

    store.insert_zone(Zone(zone_id="zone_lobby", name="Lobby", zone_type="public"))

    seq1 = store.append_event(evt1)
    seq2 = store.append_event(evt2)

    assert seq1 == 1
    assert seq2 == 2
    assert evt1.sequence_num == 1
    assert evt2.sequence_num == 2

    # Query events
    events, total = store.get_events(limit=10)
    assert total == 2
    assert len(events) == 2
    # Ordered by sequence_num DESC
    assert events[0].event_id == "evt_002"
    assert events[1].event_id == "evt_001"


def test_event_idempotency_constraint():
    """Verify unique constraint on event_id prevents duplicate insertions."""
    store = TwinStore(db_path=":memory:")

    evt = TwinEvent(
        event_id="evt_duplicate_test",
        event_type="PERSON_DETECTED",
        timestamp="2026-09-19T10:00:00Z",
        track_id="trk_1",
    )
    store.append_event(evt)

    with pytest.raises(sqlite3.IntegrityError):
        store.append_event(evt)


def test_transaction_rollback():
    """Verify atomic transaction rollback when an error occurs during batch insert."""
    store = TwinStore(db_path=":memory:")

    zone = Zone(
        zone_id="z_test",
        name="Test Zone",
        zone_type="workspace",
    )
    store.insert_zone(zone)

    # Attempt inserting invalid vertices where one violates foreign key inside transaction
    with pytest.raises(sqlite3.IntegrityError):
        with store._lock, store.get_connection() as conn:
            try:
                conn.execute("INSERT INTO zone_vertices (zone_id, vertex_order, x, y) VALUES ('z_test', 0, 0.0, 0.0);")
                # Invalid zone_id triggers foreign key constraint violation
                conn.execute("INSERT INTO zone_vertices (zone_id, vertex_order, x, y) VALUES ('non_existent_zone', 1, 1.0, 1.0);")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # Verify first vertex was rolled back and not persisted
    vertices = store.get_zone_vertices("z_test")
    assert len(vertices) == 0
