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
    assert len(rows) == 2
    assert rows[-1][0] == 2

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


def test_fresh_db_ends_at_version_2():
    """Verify fresh database runs all migrations and ends at version 2."""
    conn = sqlite3.connect(":memory:")
    applied = run_migrations(conn)
    assert applied == [1, 2]
    cur = conn.execute("SELECT MAX(version) FROM schema_migrations;")
    assert cur.fetchone()[0] == 2


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

    # Run migrations - should apply only v2
    applied = run_migrations(conn)
    assert applied == [2]

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

