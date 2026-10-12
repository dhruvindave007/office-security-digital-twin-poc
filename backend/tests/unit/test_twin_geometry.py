from src.twin.geometry import GeometryEngine
from src.twin.models import Zone


def test_geometry_engine_point_resolution():
    """Verify STRtree spatial indexing accurately resolves points into zones."""
    z_reception = Zone(
        zone_id="zone_reception",
        name="Reception",
        zone_type="public",
        security_level=1,
        geometry_version=1,
    )
    z_server = Zone(
        zone_id="zone_server_room",
        name="Server Room",
        zone_type="secure",
        security_level=4,
        geometry_version=1,
    )

    vertices = {
        "zone_reception": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        "zone_server_room": [(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0)],
    }

    engine = GeometryEngine([z_reception, z_server], vertices)
    assert engine.geometry_version == 1

    # Point inside reception
    assert engine.find_zone(5.0, 5.0) == "zone_reception"

    # Point inside server room
    assert engine.find_zone(25.0, 5.0) == "zone_server_room"

    # Point outside all zones
    assert engine.find_zone(15.0, 5.0) is None
    assert engine.find_zone(100.0, 100.0) is None


def test_geometry_overlapping_priority():
    """Verify zone with higher security_level takes precedence in overlapping regions."""
    z_workspace = Zone(
        zone_id="zone_workspace",
        name="Open Workspace",
        zone_type="workspace",
        security_level=2,
    )
    z_vault = Zone(
        zone_id="zone_vault",
        name="High Security Vault",
        zone_type="secure",
        security_level=5,
    )

    # Vault is physically situated inside the workspace
    vertices = {
        "zone_workspace": [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)],
        "zone_vault": [(5.0, 5.0), (10.0, 5.0), (10.0, 10.0), (5.0, 10.0)],
    }

    engine = GeometryEngine([z_workspace, z_vault], vertices)

    # Point inside the vault region -> security_level 5 wins over security_level 2
    assert engine.find_zone(7.0, 7.0) == "zone_vault"

    # Point outside the vault but inside workspace -> workspace
    assert engine.find_zone(15.0, 15.0) == "zone_workspace"

    # Multiple zone query returns both
    all_zones = engine.find_all_zones(7.0, 7.0)
    assert set(all_zones) == {"zone_workspace", "zone_vault"}
