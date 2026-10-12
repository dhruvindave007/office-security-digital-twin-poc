import pytest

from src.twin.engine import DigitalTwinEngine
from src.twin.geometry import GeometryEngine
from src.twin.models import Camera, Zone
from src.twin.state import TwinState
from src.twin.store import TwinStore
from src.vision.geometry import (
    BoundingBox2D,
    CameraObservation,
    FloorPoint,
    PixelPoint,
    TwinObservation,
)
from src.vision.pipeline import ObservationPipeline
from src.vision.seed import VisionSeedLoader
from src.vision.zones import VisionZoneResolver


@pytest.fixture
def integrated_pipeline():
    """Setup full integrated observation pipeline backed by in-memory TwinStore."""
    store = TwinStore(db_path=":memory:")

    # Setup zones and cameras in TwinStore
    z_rec = Zone(zone_id="zone_reception", name="Reception", zone_type="public", security_level=1)
    z_work = Zone(zone_id="zone_open_workspace", name="Workspace", zone_type="workspace", security_level=2)
    z_srv = Zone(zone_id="zone_server_room", name="Server Room", zone_type="secure", security_level=4)
    store.insert_zone(z_rec)
    store.insert_zone(z_work)
    store.insert_zone(z_srv)

    from src.twin.models import ZoneVertex
    verts_rec = [(0.0, 0.0), (12.0, 0.0), (12.0, 10.0), (0.0, 10.0)]
    verts_work = [(12.0, 0.0), (30.0, 0.0), (30.0, 18.0), (12.0, 18.0)]
    verts_srv = [(30.0, 0.0), (40.0, 0.0), (40.0, 10.0), (30.0, 10.0)]

    for zid, vlist in [("zone_reception", verts_rec), ("zone_open_workspace", verts_work), ("zone_server_room", verts_srv)]:
        store.insert_vertices([ZoneVertex(zid, idx, x, y) for idx, (x, y) in enumerate(vlist)])

    store.insert_camera(Camera("cam_reception_01", "Lobby Main Entrance", "zone_reception"))
    store.insert_camera(Camera("cam_workspace_01", "Workspace West Overview", "zone_open_workspace"))
    store.insert_camera(Camera("cam_server_01", "Server Room Access Vault", "zone_server_room"))

    # Twin Engine
    twin_geom = GeometryEngine(
        zones=[z_rec, z_work, z_srv],
        vertices_by_zone={"zone_reception": verts_rec, "zone_open_workspace": verts_work, "zone_server_room": verts_srv},
    )
    twin_state = TwinState()
    twin_engine = DigitalTwinEngine(store=store, state=twin_state, geometry=twin_geom)

    # Vision components
    homography_mgr, graph, topology_service = VisionSeedLoader.load_all()
    zone_resolver = VisionZoneResolver.from_twin_store(store)

    pipeline = ObservationPipeline(
        homography_manager=homography_mgr,
        zone_resolver=zone_resolver,
        twin_engine=twin_engine,
        topology_service=topology_service,
    )

    return pipeline, store, twin_state


def test_pipeline_end_to_end_observation(integrated_pipeline):
    """Verify CameraObservation is projected, zone-resolved, and pushed into DigitalTwinEngine."""
    pipeline, store, twin_state = integrated_pipeline

    # Simulate camera detection bbox in reception: bottom center at (960, 540)
    bbox = BoundingBox2D(u_min=910.0, v_min=400.0, u_max=1010.0, v_max=540.0)
    obs = CameraObservation(
        camera_id="cam_reception_01",
        track_id="trk_guest_101",
        bbox=bbox,
        confidence=0.92,
        timestamp="2026-09-19T10:00:00Z",
    )

    res = pipeline.process_observation(obs)

    assert isinstance(res, TwinObservation)
    assert res.track_id == "trk_guest_101"
    assert res.camera_id == "cam_reception_01"
    assert res.zone_id == "zone_reception"
    assert res.pixel_point.u == 960.0
    assert res.pixel_point.v == 540.0

    # Floor coordinates must be within reception bounds [0, 12] x [0, 10]
    assert 0.0 <= res.floor_point.x <= 12.0
    assert 0.0 <= res.floor_point.y <= 10.0

    # Emitted events should include PERSON_DETECTED and ZONE_ENTERED
    event_types = [e.event_type for e in res.emitted_events]
    assert "PERSON_DETECTED" in event_types
    assert "ZONE_ENTERED" in event_types

    # In-memory Twin state must be updated
    active_tracks = twin_state.get_active_tracks()
    assert len(active_tracks) == 1
    assert active_tracks[0].track_id == "trk_guest_101"
    assert active_tracks[0].current_zone_id == "zone_reception"

    # SQLite ledger must contain the events
    events, total = store.get_events(limit=10)
    assert total == 2
    assert len(events) == 2


def test_pipeline_cross_camera_transition(integrated_pipeline):
    """Verify a person moving from reception camera to workspace camera triggers cross-zone events."""
    pipeline, store, twin_state = integrated_pipeline

    # 1. Observation on Reception camera
    obs1 = CameraObservation(
        camera_id="cam_reception_01",
        track_id="trk_emp_01",
        bottom_center=PixelPoint(u=960.0, v=540.0),
        timestamp="2026-09-19T10:00:00Z",
    )
    pipeline.process_observation(obs1)

    # 2. Observation 8 seconds later on Workspace camera
    obs2 = CameraObservation(
        camera_id="cam_workspace_01",
        track_id="trk_emp_01",
        bottom_center=PixelPoint(u=960.0, v=540.0),
        timestamp="2026-09-19T10:00:08Z",
    )
    res2 = pipeline.process_observation(obs2)

    assert res2.zone_id == "zone_open_workspace"
    event_types = [e.event_type for e in res2.emitted_events]
    assert "PERSON_DETECTED" in event_types
    assert "CROSS_ZONE_TRANSITION" in event_types
    assert "ZONE_ENTERED" in event_types

    # In-memory Twin state reflects new zone and single active track
    active = twin_state.get_active_tracks()
    assert len(active) == 1
    assert active[0].current_zone_id == "zone_open_workspace"
    assert active[0].current_camera_id == "cam_workspace_01"

    # Emitted cross-zone transition event records previous and new zones
    trans_evt = next(e for e in res2.emitted_events if e.event_type == "CROSS_ZONE_TRANSITION")
    assert trans_evt.previous_zone_id == "zone_reception"
    assert trans_evt.zone_id == "zone_open_workspace"
