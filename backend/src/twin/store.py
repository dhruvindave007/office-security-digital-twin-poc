from contextlib import contextmanager
from datetime import datetime
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, Generator, List, Optional, Tuple

from src.core.timeutil import canonical_utc
from src.twin.migrations import run_migrations
from src.twin.models import (
    Camera,
    Incident,
    TwinEvent,
    TwinStateSnapshot,
    Zone,
    ZoneVertex,
)

logger = logging.getLogger("office_vision.twin.store")


class TwinStore:
    """Dedicated SQLite repository layer managing relational data and the event ledger.

    Strictly separates database persistence and transaction isolation from state machines.
    Enforces WAL mode, foreign keys, synchronous=NORMAL, and busy_timeout=5000.
    """

    def __init__(self, db_path: str = "data/digital_twin.db"):
        self.db_path = db_path
        self._is_memory = db_path == ":memory:" or "mode=memory" in db_path
        self._lock = threading.Lock()

        if self._is_memory:
            self._real_path = ":memory:"
            self._conn: Optional[sqlite3.Connection] = sqlite3.connect(
                ":memory:",
                check_same_thread=False,
                timeout=5.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._apply_pragmas(self._conn)
        else:
            clean_path = db_path.replace("sqlite:///", "")
            Path(clean_path).parent.mkdir(parents=True, exist_ok=True)
            self._real_path = clean_path
            self._conn = None

        self._init_db()

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """Apply mandatory SQLite pragmas."""
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an active SQLite connection configured with required pragmas."""
        if self._is_memory:
            yield self._conn
        else:
            conn = sqlite3.connect(
                self._real_path,
                check_same_thread=False,
                timeout=5.0,
            )
            conn.row_factory = sqlite3.Row
            self._apply_pragmas(conn)
            try:
                yield conn
            finally:
                conn.close()

    def close(self) -> None:
        """Close persistent in-memory connection if active."""
        if self._is_memory and self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init_db(self) -> None:
        """Run pending schema migrations."""
        with self.get_connection() as conn:
            run_migrations(conn)

    # -------------------------------------------------------------------------
    # Zones & Vertices
    # -------------------------------------------------------------------------
    def insert_zone(self, zone: Zone) -> None:
        """Insert or replace a zone record with pre-computed bounding box and version."""
        with self._lock, self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO zones (
                    zone_id, name, zone_type, security_level, max_occupancy,
                    min_x, min_y, max_x, max_y, geometry_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    zone.zone_id,
                    zone.name,
                    zone.zone_type,
                    zone.security_level,
                    zone.max_occupancy,
                    zone.min_x,
                    zone.min_y,
                    zone.max_x,
                    zone.max_y,
                    zone.geometry_version,
                    zone.created_at,
                    zone.updated_at,
                ),
            )
            conn.commit()

    def get_zones(self) -> List[Zone]:
        """Fetch all zones ordered by security level descending."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM zones ORDER BY security_level DESC, zone_id ASC;")
            return [
                Zone(
                    zone_id=row["zone_id"],
                    name=row["name"],
                    zone_type=row["zone_type"],
                    security_level=row["security_level"],
                    max_occupancy=row["max_occupancy"],
                    min_x=row["min_x"],
                    min_y=row["min_y"],
                    max_x=row["max_x"],
                    max_y=row["max_y"],
                    geometry_version=row["geometry_version"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in cur.fetchall()
            ]

    def get_zone(self, zone_id: str) -> Optional[Zone]:
        """Fetch a single zone by identifier."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM zones WHERE zone_id = ?;", (zone_id,))
            row = cur.fetchone()
            if not row:
                return None
            return Zone(
                zone_id=row["zone_id"],
                name=row["name"],
                zone_type=row["zone_type"],
                security_level=row["security_level"],
                max_occupancy=row["max_occupancy"],
                min_x=row["min_x"],
                min_y=row["min_y"],
                max_x=row["max_x"],
                max_y=row["max_y"],
                geometry_version=row["geometry_version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def insert_vertices(self, vertices: List[ZoneVertex]) -> None:
        """Insert a batch of ordered zone boundary vertices inside an atomic transaction."""
        if not vertices:
            return
        with self._lock, self.get_connection() as conn:
            for v in vertices:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO zone_vertices (zone_id, vertex_order, x, y)
                    VALUES (?, ?, ?, ?);
                    """,
                    (v.zone_id, v.vertex_order, v.x, v.y),
                )
            conn.commit()

    def get_zone_vertices(self, zone_id: str) -> List[ZoneVertex]:
        """Fetch ordered vertices for a given zone."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM zone_vertices WHERE zone_id = ? ORDER BY vertex_order ASC;",
                (zone_id,),
            )
            return [
                ZoneVertex(
                    vertex_id=row["vertex_id"],
                    zone_id=row["zone_id"],
                    vertex_order=row["vertex_order"],
                    x=row["x"],
                    y=row["y"],
                )
                for row in cur.fetchall()
            ]

    def get_all_vertices_by_zone(self) -> Dict[str, List[ZoneVertex]]:
        """Fetch vertices grouped by zone_id."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM zone_vertices ORDER BY zone_id, vertex_order ASC;")
            result: Dict[str, List[ZoneVertex]] = {}
            for row in cur.fetchall():
                v = ZoneVertex(
                    vertex_id=row["vertex_id"],
                    zone_id=row["zone_id"],
                    vertex_order=row["vertex_order"],
                    x=row["x"],
                    y=row["y"],
                )
                result.setdefault(v.zone_id, []).append(v)
            return result

    # -------------------------------------------------------------------------
    # Cameras
    # -------------------------------------------------------------------------
    def insert_camera(self, camera: Camera) -> None:
        """Insert or replace a camera definition."""
        with self._lock, self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cameras (
                    camera_id, name, primary_zone_id, stream_url,
                    location_description, status, fov_polygon_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    camera.camera_id,
                    camera.name,
                    camera.primary_zone_id,
                    camera.stream_url,
                    camera.location_description,
                    camera.status,
                    camera.fov_polygon_json,
                    camera.created_at,
                ),
            )
            conn.commit()

    def get_cameras(self) -> List[Camera]:
        """Fetch all camera configurations."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM cameras ORDER BY camera_id ASC;")
            return [
                Camera(
                    camera_id=row["camera_id"],
                    name=row["name"],
                    primary_zone_id=row["primary_zone_id"],
                    stream_url=row["stream_url"],
                    location_description=row["location_description"],
                    status=row["status"],
                    fov_polygon_json=row["fov_polygon_json"],
                    created_at=row["created_at"],
                )
                for row in cur.fetchall()
            ]

    # -------------------------------------------------------------------------
    # Immutable Event Ledger (Append-Only)
    # -------------------------------------------------------------------------
    def append_event(self, event: TwinEvent) -> int:
        """Append an immutable event to the ledger.

        Strictly append-only. Returns assigned sequence_num. The timestamp is normalised to the
        canonical fixed-width UTC format (also on the passed-in event object).
        """
        event.timestamp = canonical_utc(event.timestamp)
        with self._lock, self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (
                    event_id, event_type, timestamp, track_id, person_id,
                    camera_id, zone_id, previous_zone_id, confidence,
                    coordinates_json, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.timestamp,
                    event.track_id,
                    event.person_id,
                    event.camera_id,
                    event.zone_id,
                    event.previous_zone_id,
                    event.confidence,
                    event.coordinates_json,
                    event.payload_json,
                    event.created_at,
                ),
            )
            seq_num = cur.lastrowid
            conn.commit()
            event.sequence_num = seq_num
            return seq_num

    def get_events(
        self,
        limit: int = 50,
        offset: int = 0,
        zone_id: Optional[str] = None,
        track_id: Optional[str] = None,
        event_type: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Tuple[List[TwinEvent], int]:
        """Query events with indexed filtering and return (events, total_count)."""
        clauses = []
        params: List[Any] = []

        if zone_id:
            clauses.append("zone_id = ?")
            params.append(zone_id)
        if track_id:
            clauses.append("track_id = ?")
            params.append(track_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if start_time:
            clauses.append("timestamp >= ?")
            params.append(canonical_utc(start_time))  # ValueError on malformed input
        if end_time:
            clauses.append("timestamp <= ?")
            params.append(canonical_utc(end_time))

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self.get_connection() as conn:
            # Count total matching rows
            cur_count = conn.execute(f"SELECT COUNT(*) FROM events {where_sql};", params)
            total = cur_count.fetchone()[0]

            # Query page
            page_params = list(params) + [limit, offset]
            cur_rows = conn.execute(
                f"SELECT * FROM events {where_sql} ORDER BY sequence_num DESC LIMIT ? OFFSET ?;",
                page_params,
            )
            events = [
                TwinEvent(
                    sequence_num=row["sequence_num"],
                    event_id=row["event_id"],
                    event_type=row["event_type"],
                    timestamp=row["timestamp"],
                    track_id=row["track_id"],
                    person_id=row["person_id"],
                    camera_id=row["camera_id"],
                    zone_id=row["zone_id"],
                    previous_zone_id=row["previous_zone_id"],
                    confidence=row["confidence"],
                    coordinates_json=row["coordinates_json"],
                    payload_json=row["payload_json"],
                    created_at=row["created_at"],
                )
                for row in cur_rows.fetchall()
            ]
            return events, total

    def get_events_since(self, sequence_num: int) -> List[TwinEvent]:
        """Fetch all events strictly greater than sequence_num in ascending order (for replay)."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM events WHERE sequence_num > ? ORDER BY sequence_num ASC;",
                (sequence_num,),
            )
            return [self._row_to_event(row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> TwinEvent:
        return TwinEvent(
            sequence_num=row["sequence_num"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            timestamp=row["timestamp"],
            track_id=row["track_id"],
            person_id=row["person_id"],
            camera_id=row["camera_id"],
            zone_id=row["zone_id"],
            previous_zone_id=row["previous_zone_id"],
            confidence=row["confidence"],
            coordinates_json=row["coordinates_json"],
            payload_json=row["payload_json"],
            created_at=row["created_at"],
        )

    # -------------------------------------------------------------------------
    # Incidents
    # -------------------------------------------------------------------------
    def insert_incident(self, incident: Incident) -> None:
        """Insert or replace a security incident record (time fields normalised to canonical UTC)."""
        incident.start_time = canonical_utc(incident.start_time)
        incident.created_at = canonical_utc(incident.created_at)
        incident.updated_at = canonical_utc(incident.updated_at)
        if incident.end_time:
            incident.end_time = canonical_utc(incident.end_time)
        with self._lock, self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO incidents (
                    incident_id, incident_type, severity, status, zone_id,
                    track_id, person_id, start_time, end_time,
                    supporting_event_ids_json, summary, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    incident.incident_id,
                    incident.incident_type,
                    incident.severity,
                    incident.status,
                    incident.zone_id,
                    incident.track_id,
                    incident.person_id,
                    incident.start_time,
                    incident.end_time,
                    incident.supporting_event_ids_json,
                    incident.summary,
                    incident.created_at,
                    incident.updated_at,
                ),
            )
            conn.commit()

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        """Map SQLite row to Incident domain model."""
        return Incident(
            incident_id=row["incident_id"],
            incident_type=row["incident_type"],
            severity=row["severity"],
            status=row["status"],
            zone_id=row["zone_id"],
            track_id=row["track_id"],
            person_id=row["person_id"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            supporting_event_ids_json=row["supporting_event_ids_json"],
            summary=row["summary"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Fetch a single security incident by its identifier."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM incidents WHERE incident_id = ? LIMIT 1;",
                (incident_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_incident(row)

    def get_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        zone_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Incident]:
        """Query security incidents with status/severity filters."""
        clauses = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if zone_id:
            clauses.append("zone_id = ?")
            params.append(zone_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with self.get_connection() as conn:
            cur = conn.execute(
                f"SELECT * FROM incidents {where_sql} ORDER BY start_time DESC LIMIT ?;",
                params,
            )
            return [self._row_to_incident(row) for row in cur.fetchall()]

    def get_active_incidents(self) -> List[Incident]:
        """ALL incidents with status OPEN or INVESTIGATING (no limit), oldest first."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM incidents WHERE status IN ('OPEN', 'INVESTIGATING') ORDER BY start_time ASC;"
            )
            return [self._row_to_incident(row) for row in cur.fetchall()]

    def count_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        zone_id: Optional[str] = None,
    ) -> int:
        """Total number of incidents matching the same filters as get_incidents (ignores limit)."""
        clauses, params = [], []
        for column, value in (("status", status), ("severity", severity), ("zone_id", zone_id)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.get_connection() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM incidents {where_sql};", params).fetchone()[0]

    def get_events_ascending(
        self,
        track_id: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        zone_id: Optional[str] = None,
        track_ids: Optional[List[str]] = None,
    ) -> List[TwinEvent]:
        """ALL matching events in ledger order (sequence_num ASC), no limit.

        For history computations that must not silently drop old events. Time bounds are
        inclusive and normalised to canonical UTC (ValueError on malformed input).
        """
        clauses: List[str] = []
        params: List[Any] = []
        if track_id:
            clauses.append("track_id = ?")
            params.append(track_id)
        if track_ids:
            clauses.append(f"track_id IN ({', '.join('?' for _ in track_ids)})")
            params.extend(track_ids)
        if zone_id:
            clauses.append("zone_id = ?")
            params.append(zone_id)
        if event_types:
            clauses.append(f"event_type IN ({', '.join('?' for _ in event_types)})")
            params.extend(event_types)
        if start_time:
            clauses.append("timestamp >= ?")
            params.append(canonical_utc(start_time))
        if end_time:
            clauses.append("timestamp <= ?")
            params.append(canonical_utc(end_time))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.get_connection() as conn:
            cur = conn.execute(f"SELECT * FROM events {where_sql} ORDER BY sequence_num ASC;", params)
            return [self._row_to_event(row) for row in cur.fetchall()]

    # -------------------------------------------------------------------------
    # AI investigations (append-only audit of every Bedrock investigation)
    # -------------------------------------------------------------------------
    _INVESTIGATION_COLUMNS = (
        "investigation_id", "incident_id", "created_at", "status", "model_id", "question",
        "result_json", "raw_output", "verification_json", "evidence_event_count",
        "input_tokens", "output_tokens", "cost_usd", "latency_ms", "error",
    )

    def insert_investigation(self, record: Dict[str, Any]) -> None:
        """Persist one investigation record (keys = _INVESTIGATION_COLUMNS)."""
        row = dict(record)
        row["created_at"] = canonical_utc(row["created_at"])
        cols = self._INVESTIGATION_COLUMNS
        with self._lock, self.get_connection() as conn:
            conn.execute(
                f"INSERT INTO investigations ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)});",
                [row.get(c) for c in cols],
            )
            conn.commit()

    def get_investigations(self, incident_id: str) -> List[Dict[str, Any]]:
        """All investigations for an incident, newest first."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM investigations WHERE incident_id = ? ORDER BY created_at DESC;",
                (incident_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def sum_investigation_cost_since(self, since_iso: str) -> float:
        """Total Bedrock spend (USD) of investigations created at or after since_iso."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM investigations WHERE created_at >= ?;",
                (canonical_utc(since_iso),),
            )
            return float(cur.fetchone()[0])

    # -------------------------------------------------------------------------
    # Snapshots (Checkpoints with Checksum)
    # -------------------------------------------------------------------------
    def save_snapshot(self, snapshot: TwinStateSnapshot) -> None:
        """Persist a state snapshot checkpoint with checksum."""
        with self._lock, self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    snapshot_id, sequence_num, timestamp, state_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.sequence_num,
                    snapshot.timestamp,
                    snapshot.state_json,
                    snapshot.checksum,
                    snapshot.created_at,
                ),
            )
            conn.commit()

    def get_latest_snapshot(self) -> Optional[TwinStateSnapshot]:
        """Fetch the most recent snapshot checkpoint by sequence_num."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM snapshots ORDER BY sequence_num DESC LIMIT 1;")
            row = cur.fetchone()
            if not row:
                return None
            return TwinStateSnapshot(
                snapshot_id=row["snapshot_id"],
                sequence_num=row["sequence_num"],
                timestamp=row["timestamp"],
                state_json=row["state_json"],
                checksum=row["checksum"],
                created_at=row["created_at"],
            )
