from datetime import datetime
import logging
import sqlite3
from typing import List, Tuple

logger = logging.getLogger("office_vision.twin.migrations")

# (version, description, sql_script)
MIGRATIONS: List[Tuple[int, str, str]] = [
    (
        1,
        "Initial normalized Digital Twin schema (zones, vertices, cameras, events, incidents, snapshots)",
        """
        -- Schema migrations tracking
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );

        -- Zones table with pre-computed bounding boxes and geometry versioning
        CREATE TABLE IF NOT EXISTS zones (
            zone_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            zone_type TEXT NOT NULL CHECK(zone_type IN ('public', 'restricted', 'secure', 'workspace', 'corridor', 'egress')),
            security_level INTEGER NOT NULL DEFAULT 1 CHECK(security_level BETWEEN 1 AND 5),
            max_occupancy INTEGER DEFAULT NULL CHECK(max_occupancy IS NULL OR max_occupancy >= 0),
            min_x REAL NOT NULL DEFAULT 0.0,
            min_y REAL NOT NULL DEFAULT 0.0,
            max_x REAL NOT NULL DEFAULT 0.0,
            max_y REAL NOT NULL DEFAULT 0.0,
            geometry_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        -- Zone vertices table
        CREATE TABLE IF NOT EXISTS zone_vertices (
            vertex_id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_id TEXT NOT NULL REFERENCES zones(zone_id) ON DELETE CASCADE,
            vertex_order INTEGER NOT NULL CHECK(vertex_order >= 0),
            x REAL NOT NULL,
            y REAL NOT NULL,
            CONSTRAINT uq_zone_vertex_order UNIQUE (zone_id, vertex_order)
        );

        CREATE INDEX IF NOT EXISTS idx_zone_vertices_zone_order 
        ON zone_vertices(zone_id, vertex_order);

        -- Cameras table
        CREATE TABLE IF NOT EXISTS cameras (
            camera_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            primary_zone_id TEXT REFERENCES zones(zone_id) ON DELETE SET NULL,
            stream_url TEXT DEFAULT NULL,
            location_description TEXT DEFAULT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'maintenance', 'offline')),
            fov_polygon_json TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_cameras_primary_zone 
        ON cameras(primary_zone_id);

        -- Immutable Append-Only Event Ledger
        CREATE TABLE IF NOT EXISTS events (
            sequence_num INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'PERSON_DETECTED',
                'ZONE_ENTERED',
                'ZONE_EXITED',
                'CROSS_ZONE_TRANSITION',
                'DWELL_TIME_EXCEEDED',
                'AFTER_HOURS_PRESENCE',
                'UNAUTHORIZED_ACCESS',
                'TAILGATING_SUSPECTED',
                'TRACK_LOST'
            )),
            timestamp TEXT NOT NULL,
            track_id TEXT NOT NULL,
            person_id TEXT DEFAULT NULL,
            camera_id TEXT REFERENCES cameras(camera_id) ON DELETE SET NULL,
            zone_id TEXT REFERENCES zones(zone_id) ON DELETE SET NULL,
            previous_zone_id TEXT REFERENCES zones(zone_id) ON DELETE SET NULL,
            confidence REAL DEFAULT NULL CHECK(confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
            coordinates_json TEXT DEFAULT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_events_timestamp 
        ON events(timestamp ASC);

        CREATE INDEX IF NOT EXISTS idx_events_track_id 
        ON events(track_id, timestamp ASC);

        CREATE INDEX IF NOT EXISTS idx_events_zone_id 
        ON events(zone_id, timestamp ASC);

        CREATE INDEX IF NOT EXISTS idx_events_event_type 
        ON events(event_type, timestamp ASC);

        -- Incidents table
        CREATE TABLE IF NOT EXISTS incidents (
            incident_id TEXT PRIMARY KEY,
            incident_type TEXT NOT NULL CHECK(incident_type IN (
                'UNAUTHORIZED_ENTRY',
                'TAILGATING',
                'DWELL_ANOMALY',
                'AFTER_HOURS_BREACH',
                'TRACK_DISCONTINUITY'
            )),
            severity TEXT NOT NULL CHECK(severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
            status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'FALSE_POSITIVE')),
            zone_id TEXT REFERENCES zones(zone_id) ON DELETE SET NULL,
            track_id TEXT DEFAULT NULL,
            person_id TEXT DEFAULT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT NULL,
            supporting_event_ids_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_incidents_status_severity 
        ON incidents(status, severity);

        CREATE INDEX IF NOT EXISTS idx_incidents_zone_id 
        ON incidents(zone_id, start_time DESC);

        CREATE INDEX IF NOT EXISTS idx_incidents_start_time 
        ON incidents(start_time DESC);

        -- Snapshots table for checkpoint restoration
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY,
            sequence_num INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            state_json TEXT NOT NULL,
            checksum TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_seq 
        ON snapshots(sequence_num DESC);
        """
    ),
    (
        2,
        "Allow ZONE_OVER_CAPACITY incident type",
        """
        -- Incidents table v2 with ZONE_OVER_CAPACITY allowed
        CREATE TABLE IF NOT EXISTS incidents_v2 (
            incident_id TEXT PRIMARY KEY,
            incident_type TEXT NOT NULL CHECK(incident_type IN (
                'UNAUTHORIZED_ENTRY',
                'TAILGATING',
                'DWELL_ANOMALY',
                'AFTER_HOURS_BREACH',
                'TRACK_DISCONTINUITY',
                'ZONE_OVER_CAPACITY'
            )),
            severity TEXT NOT NULL CHECK(severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
            status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'FALSE_POSITIVE')),
            zone_id TEXT REFERENCES zones(zone_id) ON DELETE SET NULL,
            track_id TEXT DEFAULT NULL,
            person_id TEXT DEFAULT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT NULL,
            supporting_event_ids_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        INSERT INTO incidents_v2 SELECT * FROM incidents;

        DROP TABLE incidents;

        ALTER TABLE incidents_v2 RENAME TO incidents;

        CREATE INDEX IF NOT EXISTS idx_incidents_status_severity 
        ON incidents(status, severity);

        CREATE INDEX IF NOT EXISTS idx_incidents_zone_id 
        ON incidents(zone_id, start_time DESC);

        CREATE INDEX IF NOT EXISTS idx_incidents_start_time 
        ON incidents(start_time DESC);
        """,
    ),
]


def run_migrations(conn: sqlite3.Connection) -> List[int]:
    """Execute pending schema migrations in atomic order.

    Ensures foreign keys are enabled and runs inside an immediate transaction.
    Returns list of newly applied migration versions.
    """
    conn.execute("PRAGMA foreign_keys = ON;")
    # Ensure tracking table exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );
        """
    )

    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
    applied_versions = {row[0] for row in cur.fetchall()}

    applied_now: List[int] = []
    for version, description, sql_script in MIGRATIONS:
        if version not in applied_versions:
            logger.info("Applying migration v%d: %s", version, description)
            iso_ts = datetime.utcnow().isoformat()
            escaped_desc = description.replace("'", "''")
            script = (
                "BEGIN IMMEDIATE;\n"
                + sql_script
                + f"\nINSERT INTO schema_migrations (version, applied_at, description) VALUES ({version}, '{iso_ts}', '{escaped_desc}');\nCOMMIT;"
            )
            try:
                conn.executescript(script)
                applied_now.append(version)
            except Exception as e:
                if conn.in_transaction:
                    conn.execute("ROLLBACK;")
                logger.error("Migration v%d failed and was rolled back: %s", version, e)
                raise

    return applied_now
