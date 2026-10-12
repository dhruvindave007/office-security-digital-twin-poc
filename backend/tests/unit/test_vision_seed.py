from pathlib import Path
import pytest

from src.vision.calibration import HomographyManager
from src.vision.camera_graph import CameraAdjacencyGraph
from src.vision.seed import VisionSeedLoader
from src.vision.topology import TopologyService


def test_vision_seed_load_cameras():
    """Verify loading camera configurations from data/calibration/cameras.yaml."""
    cameras = VisionSeedLoader.load_cameras()
    assert len(cameras) >= 3

    cam_ids = {c.camera_id for c in cameras}
    assert "cam_reception_01" in cam_ids
    assert "cam_workspace_01" in cam_ids
    assert "cam_server_01" in cam_ids

    rec = next(c for c in cameras if c.camera_id == "cam_reception_01")
    assert rec.resolution_width == 1920
    assert rec.resolution_height == 1080
    assert rec.homography_file == "cam_reception_01.npy"


def test_vision_seed_load_homographies():
    """Verify loading real .npy homography matrices into HomographyManager."""
    mgr = VisionSeedLoader.load_homographies()
    assert isinstance(mgr, HomographyManager)

    assert mgr.has_camera("cam_reception_01")
    assert mgr.has_camera("cam_workspace_01")
    assert mgr.has_camera("cam_server_01")

    # Verify that projection works with loaded matrices
    from src.vision.geometry import PixelPoint
    res = mgr.project_pixel_to_floor("cam_reception_01", PixelPoint(u=960.0, v=540.0))
    # Reception floor coordinates must be within [0, 12] x [0, 10]
    assert 0.0 <= res.floor_point.x <= 12.0
    assert 0.0 <= res.floor_point.y <= 10.0


def test_vision_seed_load_topology():
    """Verify loading topology graph from data/topology/topology.yaml."""
    cameras = VisionSeedLoader.load_cameras()
    graph = VisionSeedLoader.load_topology(cameras=cameras)
    assert isinstance(graph, CameraAdjacencyGraph)

    # Reception should connect to Workspace
    out_rec = [e.to_camera_id for e in graph.get_outgoing_edges("cam_reception_01")]
    assert "cam_workspace_01" in out_rec

    # Workspace connects to Reception and Server Room
    out_work = [e.to_camera_id for e in graph.get_outgoing_edges("cam_workspace_01")]
    assert "cam_reception_01" in out_work
    assert "cam_server_01" in out_work


def test_vision_seed_load_all():
    """Verify load_all initializes and returns all components with valid connectivity."""
    homography_mgr, graph, topology_service = VisionSeedLoader.load_all()

    assert isinstance(homography_mgr, HomographyManager)
    assert isinstance(graph, CameraAdjacencyGraph)
    assert isinstance(topology_service, TopologyService)

    # Connectivity must be verified
    assert graph.validate_connectivity(require_connected=True) is True

    # Path from reception to server room exists
    path_info = topology_service.get_shortest_path("cam_reception_01", "cam_server_01")
    assert path_info is not None
    assert path_info[0] == ["cam_reception_01", "cam_workspace_01", "cam_server_01"]


def test_vision_seed_missing_files_error():
    """Verify FileNotFoundError when specifying non-existent directory."""
    with pytest.raises(FileNotFoundError):
        VisionSeedLoader.load_cameras(calibration_dir="non_existent_dir_xyz")

    with pytest.raises(FileNotFoundError):
        VisionSeedLoader.load_topology(topology_dir="non_existent_dir_xyz")
