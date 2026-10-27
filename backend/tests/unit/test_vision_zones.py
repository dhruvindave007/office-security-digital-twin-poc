# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from src.twin.models import Zone
from src.twin.store import TwinStore
from src.vision.geometry import FloorPoint
from src.vision.zones import VisionZoneResolver


@pytest.fixture
def sample_zones():
    """Create 3 zones: Public Reception, Restricted Workspace, Secure Server Room."""
    z_rec = Zone(
        zone_id="zone_reception",
        name="Reception",
        zone_type="public",
        security_level=1,
    )
    z_work = Zone(
        zone_id="zone_workspace",
        name="Workspace",
        zone_type="workspace",
        security_level=2,
    )
    z_serv = Zone(
        zone_id="zone_server",
        name="Server Room",
        zone_type="secure",
        security_level=4,
    )

    vertices = {
        "zone_reception": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        "zone_workspace": [(10.0, 0.0), (30.0, 0.0), (30.0, 20.0), (10.0, 20.0)],
        "zone_server": [(30.0, 0.0), (40.0, 0.0), (40.0, 10.0), (30.0, 10.0)],
    }

    return VisionZoneResolver(zones=[z_rec, z_work, z_serv], vertices_map=vertices)


def test_zone_resolver_resolution(sample_zones):
    """Verify points resolve to the expected containing zone."""
    # Point in reception (x=5, y=5)
    assert sample_zones.resolve_zone(FloorPoint(x=5.0, y=5.0)) == "zone_reception"

    # Point in workspace (x=20, y=10)
    assert sample_zones.resolve_zone(FloorPoint(x=20.0, y=10.0)) == "zone_workspace"

    # Point in server room (x=35, y=5)
    assert sample_zones.resolve_zone(FloorPoint(x=35.0, y=5.0)) == "zone_server"


def test_zone_resolver_outside_point(sample_zones):
    """Verify point outside all zones returns None."""
    # (x=50, y=50) is well beyond facility
    assert sample_zones.resolve_zone(FloorPoint(x=50.0, y=50.0)) is None


def test_zone_resolver_overlapping_security_priority():
    """Verify that when zones overlap, the one with highest security_level wins."""
    # Zone A (security 1) covers [0, 20] x [0, 20]
    # Zone B (security 3) covers [10, 20] x [10, 20] (nested inside A)
    zA = Zone(zone_id="zone_low", name="Low", zone_type="public", security_level=1)
    zB = Zone(zone_id="zone_high", name="High", zone_type="secure", security_level=3)

    verts = {
        "zone_low": [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)],
        "zone_high": [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)],
    }

    resolver = VisionZoneResolver(zones=[zA, zB], vertices_map=verts)

    # Point in non-overlapping area (x=5, y=5) -> zone_low
    assert resolver.resolve_zone(FloorPoint(x=5.0, y=5.0)) == "zone_low"

    # Point in overlapping area (x=15, y=15) -> zone_high (higher security)
    assert resolver.resolve_zone(FloorPoint(x=15.0, y=15.0)) == "zone_high"


def test_zone_resolver_from_twin_store():
    """Verify from_twin_store initializes resolver using persistent database models."""
    store = TwinStore(db_path=":memory:")
    zone = Zone(zone_id="z_store", name="Store Zone", zone_type="workspace", security_level=2)
    store.insert_zone(zone)

    from src.twin.models import ZoneVertex
    store.insert_vertices([
        ZoneVertex("z_store", 0, 0.0, 0.0),
        ZoneVertex("z_store", 1, 10.0, 0.0),
        ZoneVertex("z_store", 2, 10.0, 10.0),
        ZoneVertex("z_store", 3, 0.0, 10.0),
    ])

    resolver = VisionZoneResolver.from_twin_store(store)
    assert resolver.resolve_zone(FloorPoint(x=2.0, y=2.0)) == "z_store"
    assert resolver.resolve_zone(FloorPoint(x=25.0, y=25.0)) is None
