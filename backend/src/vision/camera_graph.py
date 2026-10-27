# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from collections import deque
from dataclasses import dataclass, field
import logging
from typing import Dict, List, Optional, Set, Tuple

try:
    from vision.validators import CameraValidator, TopologyValidator
except ImportError:
    from src.vision.validators import CameraValidator, TopologyValidator

logger = logging.getLogger("office_vision.vision.camera_graph")


@dataclass(frozen=True)
class CameraNode:
    """Metadata describing a physical CCTV camera node in the physical office topology."""
    camera_id: str
    name: str
    primary_zone_id: Optional[str] = None
    resolution_width: int = 1920
    resolution_height: int = 1080
    fps: float = 30.0
    status: str = "active"
    location_description: Optional[str] = None
    homography_file: Optional[str] = None

    def __post_init__(self):
        CameraValidator.validate_camera_id(self.camera_id)
        CameraValidator.validate_resolution(self.resolution_width, self.resolution_height)
        CameraValidator.validate_fps(self.fps)


@dataclass(frozen=True)
class CameraEdge:
    """Directed or bidirectional transit corridor between two camera fields of view."""
    from_camera_id: str
    to_camera_id: str
    distance_meters: float
    min_transit_seconds: float
    max_transit_seconds: float
    bidirectional: bool = True

    def __post_init__(self):
        CameraValidator.validate_camera_id(self.from_camera_id)
        CameraValidator.validate_camera_id(self.to_camera_id)
        if self.from_camera_id == self.to_camera_id:
            raise ValueError(f"Self-loop edge not allowed on camera '{self.from_camera_id}'.")
        TopologyValidator.validate_distance(self.distance_meters)
        TopologyValidator.validate_transit_range(self.min_transit_seconds, self.max_transit_seconds)


class CameraAdjacencyGraph:
    """Directed graph representing spatial connectivity and pedestrian transit timing between CCTV cameras."""

    def __init__(self):
        self._nodes: Dict[str, CameraNode] = {}
        self._adj: Dict[str, List[CameraEdge]] = {}
        self._reverse_adj: Dict[str, List[CameraEdge]] = {}

    def add_node(self, node: CameraNode) -> None:
        """Register a camera node in the topology."""
        self._nodes[node.camera_id] = node
        if node.camera_id not in self._adj:
            self._adj[node.camera_id] = []
        if node.camera_id not in self._reverse_adj:
            self._reverse_adj[node.camera_id] = []

    def add_edge(self, edge: CameraEdge) -> None:
        """Add a transit corridor edge to the graph.

        If edge.bidirectional is True, also registers the reverse directional edge.
        """
        # Ensure nodes exist
        if edge.from_camera_id not in self._nodes:
            raise KeyError(f"Source camera '{edge.from_camera_id}' is not registered in the graph.")
        if edge.to_camera_id not in self._nodes:
            raise KeyError(f"Target camera '{edge.to_camera_id}' is not registered in the graph.")

        # Avoid duplicate forward edges
        existing = [e for e in self._adj[edge.from_camera_id] if e.to_camera_id == edge.to_camera_id]
        if not existing:
            self._adj[edge.from_camera_id].append(edge)
            self._reverse_adj[edge.to_camera_id].append(edge)

        # Register reverse edge if bidirectional
        if edge.bidirectional:
            rev_existing = [e for e in self._adj[edge.to_camera_id] if e.to_camera_id == edge.from_camera_id]
            if not rev_existing:
                rev_edge = CameraEdge(
                    from_camera_id=edge.to_camera_id,
                    to_camera_id=edge.from_camera_id,
                    distance_meters=edge.distance_meters,
                    min_transit_seconds=edge.min_transit_seconds,
                    max_transit_seconds=edge.max_transit_seconds,
                    bidirectional=True,
                )
                self._adj[edge.to_camera_id].append(rev_edge)
                self._reverse_adj[edge.from_camera_id].append(rev_edge)

    def validate_connectivity(self, require_connected: bool = True) -> bool:
        """Verify graph connectivity and detect orphaned/disconnected cameras.

        Raises:
            ValueError: If require_connected is True and graph contains disconnected components.
        """
        if not self._nodes:
            return True

        if len(self._nodes) == 1:
            return True

        # Perform BFS across undirected view of edges
        start_node = next(iter(self._nodes.keys()))
        visited: Set[str] = set()
        queue = deque([start_node])
        visited.add(start_node)

        while queue:
            curr = queue.popleft()
            # Outgoing neighbors
            neighbors = [e.to_camera_id for e in self._adj.get(curr, [])]
            # Incoming neighbors (for weak connectivity)
            neighbors += [e.from_camera_id for e in self._reverse_adj.get(curr, [])]

            for nbr in neighbors:
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)

        if len(visited) < len(self._nodes):
            orphans = set(self._nodes.keys()) - visited
            if require_connected:
                raise ValueError(
                    f"Camera graph is disconnected. Visited {len(visited)}/{len(self._nodes)} cameras. "
                    f"Orphaned or disconnected camera(s): {sorted(orphans)}."
                )
            return False

        return True

    def get_node(self, camera_id: str) -> Optional[CameraNode]:
        return self._nodes.get(camera_id)

    def get_nodes(self) -> List[CameraNode]:
        return list(self._nodes.values())

    def get_outgoing_edges(self, camera_id: str) -> List[CameraEdge]:
        return list(self._adj.get(camera_id, []))

    def get_all_edges(self) -> List[CameraEdge]:
        seen = set()
        edges = []
        for edge_list in self._adj.values():
            for e in edge_list:
                key = (e.from_camera_id, e.to_camera_id)
                if key not in seen:
                    seen.add(key)
                    edges.append(e)
        return edges

    def clear(self) -> None:
        self._nodes.clear()
        self._adj.clear()
        self._reverse_adj.clear()
