# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Union

try:
    from src.rules.policy import parse_utc_timestamp
    from src.twin.models import TwinEvent
except ImportError:
    from rules.policy import parse_utc_timestamp
    from twin.models import TwinEvent


def _parse_coordinates(coordinates_json: Optional[str]) -> Optional[tuple[float, float]]:
    """Parse x, y coordinate tuple from JSON string, or None if missing/invalid."""
    if not coordinates_json:
        return None
    try:
        data = json.loads(coordinates_json)
        if isinstance(data, dict) and "x" in data and "y" in data:
            return float(data["x"]), float(data["y"])
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            return float(data[0]), float(data[1])
    except Exception:
        pass
    return None


def track_path(events: List[TwinEvent]) -> List[dict]:
    """Extract ordered path points for a track from PERSON_DETECTED events.

    Pure function: no store access. Sorts events by sequence_num ascending.
    Skips events without coordinates.
    """
    sorted_events = sorted(events, key=lambda e: e.sequence_num)
    points: List[dict] = []

    for e in sorted_events:
        if e.event_type != "PERSON_DETECTED":
            continue
        coords = _parse_coordinates(e.coordinates_json)
        if coords is None:
            continue
        x, y = coords
        points.append(
            {
                "event_id": e.event_id,
                "timestamp": e.timestamp,
                "x": x,
                "y": y,
                "zone_id": e.zone_id,
                "camera_id": e.camera_id,
            }
        )

    return points


def occupancy_series(
    events: List[TwinEvent],
    start: Union[str, datetime],
    end: Union[str, datetime],
    bucket_seconds: int,
) -> List[dict]:
    """Calculate zone occupancy time series by walking ledger events across bucket boundaries.

    Pure function: no store access.
    - Input is ledger events up to end.
    - Explicitly sorted by sequence_num ascending.
    - Ignores badge:* tracks and non-occupancy event types.
    - Emits one sample at each bucket boundary from start (inclusive) to end (exclusive),
      counting state after applying all events with timestamp <= boundary.
    - Includes every zone that appears across events, with 0 when empty.
    """
    start_dt = parse_utc_timestamp(start) if isinstance(start, str) else start
    end_dt = parse_utc_timestamp(end) if isinstance(end, str) else end

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    sorted_events = sorted(events, key=lambda e: e.sequence_num)

    # Pre-discover all zones that appear in events
    all_zones: Set[str] = set()
    relevant_events = []
    for e in sorted_events:
        if e.track_id and e.track_id.startswith("badge:"):
            continue
        if e.zone_id:
            all_zones.add(e.zone_id)
        if e.event_type in ("ZONE_ENTERED", "CROSS_ZONE_TRANSITION", "ZONE_EXITED", "TRACK_LOST"):
            event_dt = parse_utc_timestamp(e.timestamp)
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
            relevant_events.append((event_dt, e))

    # Maintain sorted order by event_dt then sequence_num
    relevant_events.sort(key=lambda item: (item[0], item[1].sequence_num))

    sorted_zone_list = sorted(all_zones)
    track_to_zone: Dict[str, Optional[str]] = {}
    samples: List[dict] = []
    evt_idx = 0
    num_events = len(relevant_events)

    current_boundary = start_dt
    step = timedelta(seconds=bucket_seconds)

    while current_boundary < end_dt:
        # Advance events up to and including current_boundary
        while evt_idx < num_events and relevant_events[evt_idx][0] <= current_boundary:
            _, evt = relevant_events[evt_idx]
            evt_idx += 1
            if evt.event_type in ("ZONE_ENTERED", "CROSS_ZONE_TRANSITION"):
                track_to_zone[evt.track_id] = evt.zone_id
            elif evt.event_type in ("ZONE_EXITED", "TRACK_LOST"):
                track_to_zone[evt.track_id] = None

        # Count occupancy per zone
        occ: Dict[str, int] = {z: 0 for z in sorted_zone_list}
        for trk, z in track_to_zone.items():
            if z and z in occ:
                occ[z] += 1

        samples.append(
            {
                "bucket_start": current_boundary.isoformat(),
                "occupancy": occ,
            }
        )
        current_boundary += step

    return samples


def position_heatmap(
    events: List[TwinEvent],
    width_m: float,
    height_m: float,
    cell_size_m: float,
) -> dict:
    """Aggregate PERSON_DETECTED observations into a 2D floorplan spatial heatmap.

    Pure function: no store access.
    Grid cells are [row][col], where col = int(x // cell_size_m) and row = int(y // cell_size_m),
    clamped into grid boundaries.
    """
    cols = max(1, int(math.ceil(width_m / cell_size_m)))
    rows = max(1, int(math.ceil(height_m / cell_size_m)))
    cells = [[0 for _ in range(cols)] for _ in range(rows)]

    for e in events:
        if e.event_type != "PERSON_DETECTED":
            continue
        coords = _parse_coordinates(e.coordinates_json)
        if coords is None:
            continue
        x, y = coords
        col = max(0, min(cols - 1, int(x // cell_size_m)))
        row = max(0, min(rows - 1, int(y // cell_size_m)))
        cells[row][col] += 1

    return {
        "width_m": width_m,
        "height_m": height_m,
        "cell_size_m": cell_size_m,
        "cols": cols,
        "rows": rows,
        "cells": cells,
    }
