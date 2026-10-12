import sqlite3
import pytest
from src.twin.migrations import run_migrations


def test_migrations_execution():
    """Verify migrations run on clean in-memory database and create all required tables."""
    conn = sqlite3.connect(":memory:")
    applied = run_migrations(conn)

    assert 1 in applied

    # Check tables exist
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cur.fetchall()}

    expected_tables = {
        "schema_migrations",
        "zones",
        "zone_vertices",
        "cameras",
        "events",
        "incidents",
        "snapshots",
    }
    assert expected_tables.issubset(tables)

    # Verify migration record was recorded
    cur = conn.execute("SELECT version, description FROM schema_migrations ORDER BY version ASC;")
    rows = cur.fetchall()
    assert len(rows) == 3
    assert rows[-1][0] == 3

    # Re-running migrations should be idempotent and apply 0 new migrations
    applied_again = run_migrations(conn)
    assert len(applied_again) == 0


def test_migration_constraints_and_foreign_keys():
    """Verify foreign key enforcement and check constraints."""
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    conn.execute("PRAGMA foreign_keys = ON;")

    # Test zone_type check constraint
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO zones (zone_id, name, zone_type)
            VALUES ('z_bad', 'Bad Zone', 'invalid_type');
            """
        )

    # Insert valid zone
    conn.execute(
        """
        INSERT INTO zones (zone_id, name, zone_type, security_level)
        VALUES ('z_vault', 'Secure Vault', 'secure', 4);
        """
    )

    # Test foreign key cascade: deleting zone deletes vertices
    conn.execute(
        """
        INSERT INTO zone_vertices (zone_id, vertex_order, x, y)
        VALUES ('z_vault', 0, 0.0, 0.0);
        """
    )
    conn.execute("DELETE FROM zones WHERE zone_id = 'z_vault';")
    cur = conn.execute("SELECT COUNT(*) FROM zone_vertices WHERE zone_id = 'z_vault';")
    assert cur.fetchone()[0] == 0


def test_fresh_db_ends_at_version_3():
    """Verify fresh database runs all migrations and ends at version 3."""
    conn = sqlite3.connect(":memory:")
    applied = run_migrations(conn)
    assert applied == [1, 2, 3]
    cur = conn.execute("SELECT MAX(version) FROM schema_migrations;")
    assert cur.fetchone()[0] == 3


def test_v1_upgrade_to_v2_preserves_row():
    """Verify v1-only DB with an existing incident upgrades to v2 and keeps the row."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    from src.twin.migrations import MIGRATIONS

    # Apply only migration 1
    v1_version, v1_desc, v1_sql = MIGRATIONS[0]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );
        """
    )
    conn.executescript(v1_sql)
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
        (v1_version, "2026-01-01T00:00:00Z", v1_desc),
    )
    conn.commit()

    # Insert an existing incident into v1 table
    conn.execute(
        """
        INSERT INTO incidents (
            incident_id, incident_type, severity, status, start_time, summary
        ) VALUES (
            'inc_v1_001', 'UNAUTHORIZED_ENTRY', 'HIGH', 'OPEN', '2026-01-01T12:00:00Z', 'Test incident'
        );
        """
    )
    conn.commit()

    # Run migrations - should apply v2 and v3
    applied = run_migrations(conn)
    assert applied == [2, 3]

    # Confirm the incident row was preserved
    cur = conn.execute(
        "SELECT incident_id, incident_type, severity, status FROM incidents WHERE incident_id = 'inc_v1_001';"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "inc_v1_001"
    assert row[1] == "UNAUTHORIZED_ENTRY"
    assert row[2] == "HIGH"
    assert row[3] == "OPEN"


def test_zone_over_capacity_allowed_and_invalid_type_rejected():
    """Verify ZONE_OVER_CAPACITY succeeds on v2 and invalid types still fail."""
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)

    # Inserting ZONE_OVER_CAPACITY succeeds
    conn.execute(
        """
        INSERT INTO incidents (
            incident_id, incident_type, severity, status, start_time, summary
        ) VALUES (
            'inc_cap_001', 'ZONE_OVER_CAPACITY', 'MEDIUM', 'OPEN', '2026-01-01T12:00:00Z', 'Over capacity'
        );
        """
    )
    conn.commit()

    cur = conn.execute("SELECT incident_type FROM incidents WHERE incident_id = 'inc_cap_001';")
    assert cur.fetchone()[0] == "ZONE_OVER_CAPACITY"

    # Inserting NOT_A_TYPE still fails
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO incidents (
                incident_id, incident_type, severity, status, start_time, summary
            ) VALUES (
                'inc_bad', 'NOT_A_TYPE', 'LOW', 'OPEN', '2026-01-01T12:00:00Z', 'Bad type'
            );
            """
        )


def test_migration_atomic_rollback(monkeypatch):
    """Verify that a failing migration rolls back atomically without partial changes."""
    import src.twin.migrations as mig_mod

    conn = sqlite3.connect(":memory:")
    # Run v1, v2 and v3 first
    applied = mig_mod.run_migrations(conn)
    assert 1 in applied and 2 in applied and 3 in applied

    # Append failing migration v99
    bad_migration = (
        99,
        "Failing migration with syntax error",
        "CREATE TABLE test_rollback_table (id INTEGER PRIMARY KEY);\nINVALID SQL SYNTAX HERE;",
    )
    monkeypatch.setattr(mig_mod, "MIGRATIONS", list(mig_mod.MIGRATIONS) + [bad_migration])

    with pytest.raises(sqlite3.OperationalError):
        mig_mod.run_migrations(conn)

    # Assert test_rollback_table does not exist
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_rollback_table';")
    assert cur.fetchone() is None

    # Assert version 99 is not in schema_migrations
    cur = conn.execute("SELECT version FROM schema_migrations WHERE version = 99;")
    assert cur.fetchone() is None


def test_v2_upgrade_to_v3_preserves_events_and_sequence():
    """Verify v2 DB with events 1..5 and 1 incident upgrades to v3, keeping sequence numbers and next is 6."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    from src.twin.migrations import MIGRATIONS

    # Apply v1 and v2 manually
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );
        """
    )
    for v, desc, sql in MIGRATIONS[:2]:
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
            (v, "2026-01-01T00:00:00Z", desc),
        )
    conn.commit()

    # Insert 5 events into v2 events table
    for i in range(1, 6):
        conn.execute(
            """
            INSERT INTO events (
                event_id, event_type, timestamp, track_id
            ) VALUES (?, 'PERSON_DETECTED', '2026-01-01T12:00:00Z', 'trk_01');
            """,
            (f"evt_{i}",),
        )
    # Insert 1 incident into v2 incidents table
    conn.execute(
        """
        INSERT INTO incidents (
            incident_id, incident_type, severity, status, start_time, summary
        ) VALUES ('inc_v2_001', 'ZONE_OVER_CAPACITY', 'MEDIUM', 'OPEN', '2026-01-01T12:00:00Z', 'Summary');
        """
    )
    conn.commit()

    # Verify rows before migration
    cur = conn.execute("SELECT sequence_num, event_id FROM events ORDER BY sequence_num ASC;")
    seqs_before = [row[0] for row in cur.fetchall()]
    assert seqs_before == [1, 2, 3, 4, 5]

    # Run migrations - applies only v3
    applied = run_migrations(conn)
    assert applied == [3]

    # Check events and sequence numbers preserved
    cur = conn.execute("SELECT sequence_num, event_id FROM events ORDER BY sequence_num ASC;")
    rows = cur.fetchall()
    assert len(rows) == 5
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]

    # Check incident preserved
    cur = conn.execute("SELECT incident_id, incident_type FROM incidents WHERE incident_id = 'inc_v2_001';")
    inc_row = cur.fetchone()
    assert inc_row is not None
    assert inc_row[0] == "inc_v2_001"
    assert inc_row[1] == "ZONE_OVER_CAPACITY"

    # Append next event (v3 ACCESS_GRANTED) and verify sequence_num == 6
    cur = conn.execute(
        """
        INSERT INTO events (
            event_id, event_type, timestamp, track_id
        ) VALUES ('evt_6', 'ACCESS_GRANTED', '2026-01-01T12:01:00Z', 'badge:badge_0001');
        """
    )
    conn.commit()
    assert cur.lastrowid == 6
    cur = conn.execute("SELECT sequence_num FROM events WHERE event_id = 'evt_6';")
    assert cur.fetchone()[0] == 6


def test_v3_new_types_allowed_and_invalid_rejected():
    """Verify ACCESS_GRANTED, ACCESS_DENIED, ACCESS_WITHOUT_BADGE, BADGE_WITHOUT_ENTRY succeed and invalid fail."""
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)

    # ACCESS_GRANTED and ACCESS_DENIED in events
    conn.execute(
        """
        INSERT INTO events (event_id, event_type, timestamp, track_id)
        VALUES ('evt_ag', 'ACCESS_GRANTED', '2026-01-01T12:00:00Z', 'badge:001');
        """
    )
    conn.execute(
        """
        INSERT INTO events (event_id, event_type, timestamp, track_id)
        VALUES ('evt_ad', 'ACCESS_DENIED', '2026-01-01T12:00:00Z', 'badge:001');
        """
    )
    conn.commit()

    # Invalid event type fails
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO events (event_id, event_type, timestamp, track_id)
            VALUES ('evt_bad', 'INVALID_EVENT', '2026-01-01T12:00:00Z', 'badge:001');
            """
        )

    # ACCESS_WITHOUT_BADGE and BADGE_WITHOUT_ENTRY in incidents
    conn.execute(
        """
        INSERT INTO incidents (incident_id, incident_type, severity, status, start_time, summary)
        VALUES ('inc_awb', 'ACCESS_WITHOUT_BADGE', 'MEDIUM', 'OPEN', '2026-01-01T12:00:00Z', 'Summary');
        """
    )
    conn.execute(
        """
        INSERT INTO incidents (incident_id, incident_type, severity, status, start_time, summary)
        VALUES ('inc_bwe', 'BADGE_WITHOUT_ENTRY', 'LOW', 'OPEN', '2026-01-01T12:00:00Z', 'Summary');
        """
    )
    conn.commit()

    # Invalid incident type fails
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO incidents (incident_id, incident_type, severity, status, start_time, summary)
            VALUES ('inc_bad', 'INVALID_INCIDENT', 'LOW', 'OPEN', '2026-01-01T12:00:00Z', 'Summary');
            """
        )


def test_replay_events_leaves_twin_state_unchanged_for_badge_events():
    """Verify that replaying a ledger containing ACCESS_GRANTED events leaves TwinState unchanged for badge:* tracks."""
    from src.twin.engine import DigitalTwinEngine
    from src.twin.geometry import GeometryEngine
    from src.twin.models import TwinEvent
    from src.twin.state import TwinState
    from src.twin.store import TwinStore

    store = TwinStore(":memory:")
    geom = GeometryEngine([], {})
    state = TwinState()
    engine = DigitalTwinEngine(store=store, state=state, geometry=geom)

    # Append ACCESS_GRANTED event
    evt = TwinEvent(
        event_id="evt_badge_granted",
        event_type="ACCESS_GRANTED",
        timestamp="2026-01-15T10:00:00Z",
        track_id="badge:badge_0001",
        payload_json='{"door_id": "door_server_01", "badge_id": "badge_0001", "result": "GRANTED"}',
    )
    store.append_event(evt)

    # Fresh state and engine, replay all events
    fresh_state = TwinState()
    replay_engine = DigitalTwinEngine(store=store, state=fresh_state, geometry=geom)
    replay_engine.replay_events([evt])

    assert fresh_state.get_active_tracks() == []
    assert fresh_state.get_track("badge:badge_0001") is None



