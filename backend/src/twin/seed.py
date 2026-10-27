# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from src.twin.models import Camera, Zone, ZoneVertex
from src.twin.store import TwinStore
from src.twin.validators import PolygonValidator

logger = logging.getLogger("office_vision.twin.seed")


def load_floorplan_seed(
    store: TwinStore,
    seed_path: Optional[str] = None,
) -> bool:
    """Load floorplan definitions from YAML and seed the database idempotently.

    Verifies that seed data is loaded exactly once. If zones already exist, seeding is skipped.

    Returns:
        True if seed was applied, False if skipped (already seeded).
    """
    existing_zones = store.get_zones()
    if existing_zones:
        logger.info("Floorplan already seeded with %d zones; skipping seed step.", len(existing_zones))
        return False

    # Resolve seed file path
    if seed_path is None:
        # Default to twin package floorplan_seed.yaml
        seed_path = str(Path(__file__).parent / "floorplan_seed.yaml")
    elif not os.path.isabs(seed_path):
        # Look relative to cwd or backend
        candidate = Path(seed_path)
        if not candidate.exists():
            candidate = Path(__file__).parent / Path(seed_path).name
        seed_path = str(candidate)

    if not os.path.exists(seed_path):
        raise FileNotFoundError(f"Floorplan seed YAML file not found at: {seed_path}")

    logger.info("Loading floorplan seed from '%s'...", seed_path)
    with open(seed_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # 1. Process and validate zones and vertices
    for zone_data in data.get("zones", []):
        zone_id = zone_data["zone_id"]
        raw_vertices = zone_data.get("vertices", [])

        # Extract (x, y) coordinate tuples
        coords = [(float(v["x"]), float(v["y"])) for v in raw_vertices]

        # Validate polygon geometry and compute bounding box (Patch 2)
        validation_result = PolygonValidator.validate_vertices(coords)

        zone = Zone(
            zone_id=zone_id,
            name=zone_data["name"],
            zone_type=zone_data["zone_type"],
            security_level=int(zone_data.get("security_level", 1)),
            max_occupancy=zone_data.get("max_occupancy"),
            min_x=validation_result.min_x,
            min_y=validation_result.min_y,
            max_x=validation_result.max_x,
            max_y=validation_result.max_y,
            geometry_version=int(zone_data.get("geometry_version", 1)),
        )
        store.insert_zone(zone)

        # Insert vertices
        vertices = [
            ZoneVertex(
                zone_id=zone_id,
                vertex_order=int(v["vertex_order"]),
                x=float(v["x"]),
                y=float(v["y"]),
            )
            for v in raw_vertices
        ]
        store.insert_vertices(vertices)

    # 2. Process cameras
    for cam_data in data.get("cameras", []):
        camera = Camera(
            camera_id=cam_data["camera_id"],
            name=cam_data["name"],
            primary_zone_id=cam_data.get("primary_zone_id"),
            stream_url=cam_data.get("stream_url"),
            location_description=cam_data.get("location_description"),
            status=cam_data.get("status", "active"),
        )
        store.insert_camera(camera)

    logger.info(
        "Successfully seeded database: %d zones, %d cameras.",
        len(data.get("zones", [])),
        len(data.get("cameras", [])),
    )
    return True
