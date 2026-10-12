import pytest

from src.vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode
from src.vision.topology import TopologyService


@pytest.fixture
def sample_topology():
    """Setup a 3-camera chain: Cam1 <-> Cam2 <-> Cam3."""
    graph = CameraAdjacencyGraph()
    c1 = CameraNode(camera_id="cam_1", name="Reception")
    c2 = CameraNode(camera_id="cam_2", name="Workspace")
    c3 = CameraNode(camera_id="cam_3", name="Server Room")

    graph.add_node(c1)
    graph.add_node(c2)
    graph.add_node(c3)

    # Edge 1-2: 15m, 5s to 20s
    graph.add_edge(CameraEdge("cam_1", "cam_2", 15.0, 5.0, 20.0, bidirectional=True))
    # Edge 2-3: 10m, 3s to 15s
    graph.add_edge(CameraEdge("cam_2", "cam_3", 10.0, 3.0, 15.0, bidirectional=True))

    return TopologyService(graph=graph)


def test_topology_adjacent_cameras(sample_topology):
    """Verify get_adjacent_cameras returns direct neighbors."""
    assert sample_topology.get_adjacent_cameras("cam_1") == ["cam_2"]
    assert sample_topology.get_adjacent_cameras("cam_2") == ["cam_1", "cam_3"]
    assert sample_topology.get_adjacent_cameras("cam_3") == ["cam_2"]


def test_topology_shortest_path_direct(sample_topology):
    """Verify direct shortest path calculation."""
    res = sample_topology.get_shortest_path("cam_1", "cam_2", weight="distance")
    assert res is not None
    path, dist = res
    assert path == ["cam_1", "cam_2"]
    assert dist == 15.0


def test_topology_shortest_path_multi_hop(sample_topology):
    """Verify multi-hop shortest path calculation across intermediate camera."""
    res = sample_topology.get_shortest_path("cam_1", "cam_3", weight="distance")
    assert res is not None
    path, dist = res
    assert path == ["cam_1", "cam_2", "cam_3"]
    assert dist == 25.0


def test_topology_transit_feasibility_direct(sample_topology):
    """Verify transit feasibility on direct corridor with slack bounds."""
    # cam_1 -> cam_2: min 5s, max 20s. Default slack = 2.0s -> allowed [3.0s, 22.0s]
    assert sample_topology.is_transit_feasible("cam_1", "cam_2", elapsed_seconds=10.0) is True
    assert sample_topology.is_transit_feasible("cam_1", "cam_2", elapsed_seconds=4.0) is True

    # Impossible speed (teleportation anomaly: 1 second across 15m)
    assert sample_topology.is_transit_feasible("cam_1", "cam_2", elapsed_seconds=1.0) is False

    # Excessive duration (> 22s)
    assert sample_topology.is_transit_feasible("cam_1", "cam_2", elapsed_seconds=30.0) is False


def test_topology_transit_feasibility_multi_hop(sample_topology):
    """Verify cumulative transit feasibility across multi-hop path."""
    # cam_1 -> cam_3: total min = 5+3 = 8s, total max = 20+15 = 35s. Slack 2s -> allowed [6s, 37s]
    assert sample_topology.is_transit_feasible("cam_1", "cam_3", elapsed_seconds=15.0) is True

    # Too fast across 2 hops (3 seconds)
    assert sample_topology.is_transit_feasible("cam_1", "cam_3", elapsed_seconds=3.0) is False

    # Too slow (> 37s)
    assert sample_topology.is_transit_feasible("cam_1", "cam_3", elapsed_seconds=45.0) is False


def test_topology_reachable_cameras(sample_topology):
    """Verify get_reachable_cameras discovers reachable cameras within elapsed budget."""
    # In 7 seconds: can reach cam_1 (self) and cam_2 (min 5s - 2s = 3s), but not cam_3 (min 8s - 2s = 6s)
    reachable_short = sample_topology.get_reachable_cameras("cam_1", elapsed_seconds=4.0)
    assert "cam_1" in reachable_short
    assert "cam_2" in reachable_short
    assert "cam_3" not in reachable_short

    # In 20 seconds: can reach all 3 cameras
    reachable_all = sample_topology.get_reachable_cameras("cam_1", elapsed_seconds=20.0)
    assert reachable_all == ["cam_1", "cam_2", "cam_3"]
