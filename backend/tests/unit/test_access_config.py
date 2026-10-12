from pathlib import Path
import pytest

from src.access.config import AccessConfig, Door, load_access_config
from src.access.service import AccessControlService
from src.twin.bootstrap import TwinBootstrapService
from src.twin.models import Camera, Zone
from src.twin.store import TwinStore


def test_load_valid_access_config():
    """Verify loading default doors.yaml configuration file."""
    config = load_access_config("data/access/doors.yaml")
    assert isinstance(config, AccessConfig)
    assert len(config.doors) >= 2
    assert "door_server_01" in config.doors
    assert "door_main_entrance" in config.doors

    door_server = config.doors["door_server_01"]
    assert door_server.door_id == "door_server_01"
    assert door_server.zone_id == "zone_server_room"
    assert door_server.camera_id == "cam_server_01"
    assert door_server.badge_required is True

    door_main = config.doors["door_main_entrance"]
    assert door_main.badge_required is False

    assert config.badges.get("badge_0001") == "person_it_admin_01"
    assert config.badges.get("badge_0100") == "person_staff_01"


def test_load_access_config_duplicate_door_raises(tmp_path: Path):
    """Verify duplicate door_id raises ValueError."""
    yaml_content = """
version: 1
doors:
  - door_id: door_01
    name: "Door 1"
    zone_id: zone_a
    camera_id: cam_a
  - door_id: door_01
    name: "Duplicate Door"
    zone_id: zone_b
    camera_id: cam_b
badges: {}
"""
    p = tmp_path / "dup_doors.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate door_id"):
        load_access_config(p)


def test_service_rejects_unknown_zone_or_camera():
    """Verify AccessControlService validates that door zone_id and camera_id exist in TwinStore."""
    store = TwinStore(":memory:")
    # Seed one zone and one camera
    store.insert_zone(
        Zone(zone_id="zone_test_01", name="Test Zone", zone_type="workspace", security_level=1)
    )
    store.insert_camera(
        Camera(camera_id="cam_test_01", name="Test Camera", primary_zone_id="zone_test_01")
    )

    # 1. Door with unknown zone_id
    bad_zone_config = AccessConfig(
        doors={
            "d1": Door(
                door_id="d1",
                name="D1",
                zone_id="zone_nonexistent",
                camera_id="cam_test_01",
            )
        }
    )
    with pytest.raises(ValueError, match="zone_nonexistent"):
        AccessControlService(store=store, config=bad_zone_config)

    # 2. Door with unknown camera_id
    bad_cam_config = AccessConfig(
        doors={
            "d2": Door(
                door_id="d2",
                name="D2",
                zone_id="zone_test_01",
                camera_id="cam_nonexistent",
            )
        }
    )
    with pytest.raises(ValueError, match="cam_nonexistent"):
        AccessControlService(store=store, config=bad_cam_config)
