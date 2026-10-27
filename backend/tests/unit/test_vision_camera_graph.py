# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from src.vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode


def test_camera_graph_nodes_and_edges():
    """Verify nodes and edges can be added and queried in CameraAdjacencyGraph."""
    graph = CameraAdjacencyGraph()
    cam1 = CameraNode(camera_id="cam_01", name="Camera 1")
    cam2 = CameraNode(camera_id="cam_02", name="Camera 2")
    graph.add_node(cam1)
    graph.add_node(cam2)

    assert len(graph.get_nodes()) == 2
    assert graph.get_node("cam_01") is not None
    assert graph.get_node("cam_03") is None

    edge = CameraEdge(
        from_camera_id="cam_01",
        to_camera_id="cam_02",
        distance_meters=10.0,
        min_transit_seconds=5.0,
        max_transit_seconds=15.0,
        bidirectional=False,
    )
    graph.add_edge(edge)

    edges_1 = graph.get_outgoing_edges("cam_01")
    assert len(edges_1) == 1
    assert edges_1[0].to_camera_id == "cam_02"

    # Unidirectional: cam_02 has no outgoing edges
    assert len(graph.get_outgoing_edges("cam_02")) == 0


def test_camera_graph_bidirectional_auto_reverse():
    """Verify bidirectional edges automatically register forward and reverse edges."""
    graph = CameraAdjacencyGraph()
    graph.add_node(CameraNode(camera_id="cam_A", name="Camera A"))
    graph.add_node(CameraNode(camera_id="cam_B", name="Camera B"))

    edge = CameraEdge(
        from_camera_id="cam_A",
        to_camera_id="cam_B",
        distance_meters=15.0,
        min_transit_seconds=6.0,
        max_transit_seconds=20.0,
        bidirectional=True,
    )
    graph.add_edge(edge)

    assert len(graph.get_outgoing_edges("cam_A")) == 1
    assert graph.get_outgoing_edges("cam_A")[0].to_camera_id == "cam_B"

    assert len(graph.get_outgoing_edges("cam_B")) == 1
    assert graph.get_outgoing_edges("cam_B")[0].to_camera_id == "cam_A"


def test_camera_graph_self_loop_rejected():
    """Verify self-loop edge on a camera raises ValueError."""
    with pytest.raises(ValueError, match="Self-loop edge not allowed"):
        CameraEdge(
            from_camera_id="cam_loop",
            to_camera_id="cam_loop",
            distance_meters=5.0,
            min_transit_seconds=1.0,
            max_transit_seconds=5.0,
        )


def test_camera_graph_unregistered_node_edge_rejected():
    """Verify adding edge referencing unregistered camera node raises KeyError."""
    graph = CameraAdjacencyGraph()
    graph.add_node(CameraNode(camera_id="cam_known", name="Known Cam"))

    edge = CameraEdge(
        from_camera_id="cam_known",
        to_camera_id="cam_unknown",
        distance_meters=5.0,
        min_transit_seconds=1.0,
        max_transit_seconds=5.0,
    )
    with pytest.raises(KeyError, match="Target camera 'cam_unknown' is not registered"):
        graph.add_edge(edge)


def test_camera_graph_connectivity_valid():
    """Verify connected graph passes validate_connectivity()."""
    graph = CameraAdjacencyGraph()
    c1 = CameraNode(camera_id="c1", name="C1")
    c2 = CameraNode(camera_id="c2", name="C2")
    c3 = CameraNode(camera_id="c3", name="C3")
    graph.add_node(c1)
    graph.add_node(c2)
    graph.add_node(c3)

    graph.add_edge(CameraEdge("c1", "c2", 10.0, 3.0, 10.0, bidirectional=True))
    graph.add_edge(CameraEdge("c2", "c3", 10.0, 3.0, 10.0, bidirectional=True))

    assert graph.validate_connectivity(require_connected=True) is True


def test_camera_graph_connectivity_disconnected_rejected():
    """Verify disconnected graph with orphaned camera fails validate_connectivity()."""
    graph = CameraAdjacencyGraph()
    c1 = CameraNode(camera_id="c1", name="C1")
    c2 = CameraNode(camera_id="c2", name="C2")
    orphan = CameraNode(camera_id="c_orphan", name="Orphan")
    graph.add_node(c1)
    graph.add_node(c2)
    graph.add_node(orphan)

    # c1 and c2 are connected, but orphan is completely isolated
    graph.add_edge(CameraEdge("c1", "c2", 10.0, 3.0, 10.0, bidirectional=True))

    with pytest.raises(ValueError, match="disconnected"):
        graph.validate_connectivity(require_connected=True)
