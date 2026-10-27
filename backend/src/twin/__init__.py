# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Digital Twin Core package for Office Vision Intelligence Platform.

Provides deterministic spatial entity tracking, append-only SQLite event ledger,
Shapely 2.0 STRtree spatial indexing, thread-safe in-memory state, and snapshot checkpointing.
"""

from src.twin.bootstrap import TwinBootstrapService
from src.twin.engine import DigitalTwinEngine, generate_uuid7
from src.twin.geometry import GeometryEngine
from src.twin.models import (
    Camera,
    Incident,
    TrackedEntity,
    TwinEvent,
    TwinStateSnapshot,
    Zone,
    ZoneVertex,
)
from src.twin.seed import load_floorplan_seed
from src.twin.snapshot import SnapshotManager
from src.twin.state import TwinState
from src.twin.store import TwinStore
from src.twin.telemetry import TwinTelemetry
from src.twin.validators import EventValidator, PolygonValidator

__all__ = [
    "TwinBootstrapService",
    "DigitalTwinEngine",
    "generate_uuid7",
    "GeometryEngine",
    "TwinStore",
    "TwinState",
    "SnapshotManager",
    "TwinTelemetry",
    "PolygonValidator",
    "EventValidator",
    "load_floorplan_seed",
    "Zone",
    "ZoneVertex",
    "Camera",
    "TwinEvent",
    "Incident",
    "TrackedEntity",
    "TwinStateSnapshot",
]
