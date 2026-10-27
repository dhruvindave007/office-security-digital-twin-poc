# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml

try:
    from src.core.paths import _resolve_data_path
except ImportError:
    from core.paths import _resolve_data_path


@dataclass
class Door:
    """Access control door mapping to a physical zone and observation camera."""

    door_id: str
    name: str
    zone_id: str
    camera_id: str
    badge_required: bool = False


@dataclass
class AccessConfig:
    """Full access control configuration with doors and synthetic badge identities."""

    doors: Dict[str, Door] = field(default_factory=dict)
    badges: Dict[str, str] = field(default_factory=dict)
    version: int = 1


def load_access_config(path: Union[str, Path] = "data/access/doors.yaml") -> AccessConfig:
    """Load access control configuration from YAML, resolving path via project core paths."""
    resolved_path = _resolve_data_path(str(path), str(path))
    if not resolved_path.exists():
        raise FileNotFoundError(f"Access configuration file not found at: {path}")

    with open(resolved_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Access config YAML must contain a dictionary, got {type(data).__name__}")

    version = int(data.get("version", 1))

    raw_doors = data.get("doors", [])
    if not isinstance(raw_doors, list):
        raise ValueError(f"Access config doors must be a list, got {type(raw_doors).__name__}")

    doors: Dict[str, Door] = {}
    for d in raw_doors:
        if not isinstance(d, dict):
            raise ValueError(f"Door configuration item must be a dictionary, got {d}")
        door_id = str(d.get("door_id", "")).strip()
        if not door_id:
            raise ValueError("Door configuration missing door_id.")
        if door_id in doors:
            raise ValueError(f"Duplicate door_id found in configuration: '{door_id}'")

        name = str(d.get("name", door_id))
        zone_id = str(d.get("zone_id", "")).strip()
        camera_id = str(d.get("camera_id", "")).strip()
        badge_required = bool(d.get("badge_required", False))

        doors[door_id] = Door(
            door_id=door_id,
            name=name,
            zone_id=zone_id,
            camera_id=camera_id,
            badge_required=badge_required,
        )

    raw_badges = data.get("badges", {})
    if not isinstance(raw_badges, dict):
        raise ValueError(f"Access config badges must be a dictionary mapping, got {type(raw_badges).__name__}")
    badges = {str(k): str(v) for k, v in raw_badges.items()}

    return AccessConfig(
        doors=doors,
        badges=badges,
        version=version,
    )
