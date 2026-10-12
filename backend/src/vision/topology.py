from collections import deque
import heapq
import logging
from typing import Dict, List, Optional, Set, Tuple

try:
    from vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode
except ImportError:
    from src.vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode

logger = logging.getLogger("office_vision.vision.topology")


class TopologyService:
    """Read-only query service for camera topology, pathfinding, and transit timing feasibility."""

    def __init__(self, graph: CameraAdjacencyGraph):
        self.graph = graph

    def get_adjacent_cameras(self, camera_id: str) -> List[str]:
        """Return list of camera IDs directly reachable from camera_id."""
        edges = self.graph.get_outgoing_edges(camera_id)
        return sorted({e.to_camera_id for e in edges})

    def get_edge(self, from_camera_id: str, to_camera_id: str) -> Optional[CameraEdge]:
        """Return direct edge between two cameras, or None if no direct link exists."""
        for edge in self.graph.get_outgoing_edges(from_camera_id):
            if edge.to_camera_id == to_camera_id:
                return edge
        return None

    def get_shortest_path(
        self,
        from_camera_id: str,
        to_camera_id: str,
        weight: str = "distance",
    ) -> Optional[Tuple[List[str], float]]:
        """Compute the shortest path between two cameras using Dijkstra's algorithm.

        Args:
            from_camera_id: Starting camera ID.
            to_camera_id: Target camera ID.
            weight: Cost metric ('distance', 'min_transit', or 'avg_transit').

        Returns:
            Tuple of (camera_id_path, total_cost), or None if no path exists.
        """
        if from_camera_id == to_camera_id:
            if self.graph.get_node(from_camera_id) is not None:
                return [from_camera_id], 0.0
            return None

        if not self.graph.get_node(from_camera_id) or not self.graph.get_node(to_camera_id):
            return None

        # Priority queue: (cost, current_node, path)
        pq: List[Tuple[float, str, List[str]]] = [(0.0, from_camera_id, [from_camera_id])]
        visited: Dict[str, float] = {from_camera_id: 0.0}

        while pq:
            curr_cost, curr_node, path = heapq.heappop(pq)

            if curr_node == to_camera_id:
                return path, curr_cost

            if curr_cost > visited.get(curr_node, float("inf")):
                continue

            for edge in self.graph.get_outgoing_edges(curr_node):
                if weight == "distance":
                    edge_cost = edge.distance_meters
                elif weight == "min_transit":
                    edge_cost = edge.min_transit_seconds
                elif weight == "avg_transit":
                    edge_cost = (edge.min_transit_seconds + edge.max_transit_seconds) / 2.0
                else:
                    edge_cost = edge.distance_meters

                new_cost = curr_cost + edge_cost
                neighbor = edge.to_camera_id

                if new_cost < visited.get(neighbor, float("inf")):
                    visited[neighbor] = new_cost
                    heapq.heappush(pq, (new_cost, neighbor, path + [neighbor]))

        return None

    def is_transit_feasible(
        self,
        from_camera_id: str,
        to_camera_id: str,
        elapsed_seconds: float,
        slack_seconds: float = 2.0,
    ) -> bool:
        """Evaluate if an elapsed time interval between two camera detections is physically realistic.

        Rejects impossible speeds (e.g. teleporting across the facility in 1s)
        or excessive delays beyond max transit bounds.

        Args:
            from_camera_id: Origin camera ID.
            to_camera_id: Destination camera ID.
            elapsed_seconds: Time between last observation at origin and new observation.
            slack_seconds: Grace window for walking speed variance.
        """
        if elapsed_seconds < 0.0:
            return False

        if from_camera_id == to_camera_id:
            # Stationary track in the same camera FOV
            return True

        # Check direct edge first
        direct_edge = self.get_edge(from_camera_id, to_camera_id)
        if direct_edge:
            min_bound = max(0.0, direct_edge.min_transit_seconds - slack_seconds)
            max_bound = direct_edge.max_transit_seconds + slack_seconds
            return min_bound <= elapsed_seconds <= max_bound

        # Multi-hop transit bounds via Dijkstra
        path_info = self.get_shortest_path(from_camera_id, to_camera_id, weight="min_transit")
        if not path_info:
            # Unreachable cameras
            return False

        path, _ = path_info
        # Sum minimum and maximum transit bounds along the shortest path
        total_min = 0.0
        total_max = 0.0
        for i in range(len(path) - 1):
            edge = self.get_edge(path[i], path[i + 1])
            if edge:
                total_min += edge.min_transit_seconds
                total_max += edge.max_transit_seconds

        min_bound = max(0.0, total_min - slack_seconds)
        max_bound = total_max + slack_seconds
        return min_bound <= elapsed_seconds <= max_bound

    def get_reachable_cameras(
        self,
        from_camera_id: str,
        elapsed_seconds: float,
        slack_seconds: float = 2.0,
    ) -> List[str]:
        """Discover all cameras that could physically be reached from origin within elapsed_seconds."""
        if not self.graph.get_node(from_camera_id):
            return []

        reachable = [from_camera_id]
        for node in self.graph.get_nodes():
            cam_id = node.camera_id
            if cam_id != from_camera_id:
                if self.is_transit_feasible(from_camera_id, cam_id, elapsed_seconds, slack_seconds):
                    reachable.append(cam_id)

        return sorted(reachable)

    def min_transit_seconds(
        self,
        from_camera_id: str,
        to_camera_id: str,
    ) -> Optional[float]:
        """Compute the minimum physical transit time in seconds between two cameras.

        Returns 0.0 for the same camera, the sum of min_transit_seconds along
        the shortest path, or None if unreachable.
        """
        if from_camera_id == to_camera_id:
            if self.graph.get_node(from_camera_id) is not None:
                return 0.0
            return None

        path_info = self.get_shortest_path(from_camera_id, to_camera_id, weight="min_transit")
        if path_info is None:
            return None
        return float(path_info[1])
