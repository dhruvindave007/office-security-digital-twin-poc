import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple
import yaml

try:
    from vision.calibration import HomographyManager
    from vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode
    from vision.topology import TopologyService
except ImportError:
    from src.vision.calibration import HomographyManager
    from src.vision.camera_graph import CameraAdjacencyGraph, CameraEdge, CameraNode
    from src.vision.topology import TopologyService

logger = logging.getLogger("office_vision.vision.seed")


try:
    from src.core.paths import _resolve_data_path
except ImportError:
    from core.paths import _resolve_data_path


class VisionSeedLoader:
    """Loads camera configurations, 3x3 homography matrices, and camera topology from external files."""

    @classmethod
    def load_cameras(cls, calibration_dir: Optional[str] = None) -> List[CameraNode]:
        """Load camera definitions from cameras.yaml."""
        calib_dir = _resolve_data_path(calibration_dir or "data/calibration", "data/calibration")
        yaml_file = calib_dir / "cameras.yaml"
        if not yaml_file.exists():
            raise FileNotFoundError(f"Cameras configuration file not found at: {yaml_file}")

        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        cameras: List[CameraNode] = []
        for c in data.get("cameras", []):
            node = CameraNode(
                camera_id=c["camera_id"],
                name=c["name"],
                primary_zone_id=c.get("primary_zone_id"),
                resolution_width=int(c.get("resolution_width", 1920)),
                resolution_height=int(c.get("resolution_height", 1080)),
                fps=float(c.get("fps", 30.0)),
                status=c.get("status", "active"),
                location_description=c.get("location_description"),
                homography_file=c.get("homography_file"),
            )
            cameras.append(node)

        logger.info("Loaded %d cameras from %s", len(cameras), yaml_file)
        return cameras

    @classmethod
    def load_homographies(
        cls,
        calibration_dir: Optional[str] = None,
        geometry_version: int = 1,
    ) -> HomographyManager:
        """Load all camera homography matrices from .npy files in the calibration directory."""
        calib_dir = _resolve_data_path(calibration_dir or "data/calibration", "data/calibration")
        cameras = cls.load_cameras(calibration_dir=str(calib_dir))

        mgr = HomographyManager()
        for cam in cameras:
            if cam.homography_file:
                npy_path = calib_dir / cam.homography_file
                if not npy_path.exists():
                    raise FileNotFoundError(
                        f"Homography .npy file for camera '{cam.camera_id}' not found at: {npy_path}"
                    )
                mgr.load_homography_file(
                    camera_id=cam.camera_id,
                    file_path=npy_path,
                    geometry_version=geometry_version,
                )

        logger.info(
            "Loaded %d homographies into HomographyManager from %s",
            len(cameras),
            calib_dir,
        )
        return mgr

    @classmethod
    def load_topology(
        cls,
        topology_dir: Optional[str] = None,
        cameras: Optional[List[CameraNode]] = None,
    ) -> CameraAdjacencyGraph:
        """Load camera topology from topology.yaml and build a verified CameraAdjacencyGraph."""
        top_dir = _resolve_data_path(topology_dir or "data/topology", "data/topology")
        yaml_file = top_dir / "topology.yaml"
        if not yaml_file.exists():
            raise FileNotFoundError(f"Topology configuration file not found at: {yaml_file}")

        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        graph = CameraAdjacencyGraph()

        # If cameras list is provided, add all as nodes
        if cameras:
            for cam in cameras:
                graph.add_node(cam)

        for edge_data in data.get("edges", []):
            edge = CameraEdge(
                from_camera_id=edge_data["from_camera_id"],
                to_camera_id=edge_data["to_camera_id"],
                distance_meters=float(edge_data["distance_meters"]),
                min_transit_seconds=float(edge_data["min_transit_seconds"]),
                max_transit_seconds=float(edge_data["max_transit_seconds"]),
                bidirectional=bool(edge_data.get("bidirectional", True)),
            )
            graph.add_edge(edge)

        logger.info(
            "Loaded topology graph from %s with %d nodes and %d edges",
            yaml_file,
            len(graph.get_nodes()),
            len(graph.get_all_edges()),
        )
        return graph

    @classmethod
    def load_all(
        cls,
        calibration_dir: Optional[str] = None,
        topology_dir: Optional[str] = None,
        geometry_version: int = 1,
    ) -> Tuple[HomographyManager, CameraAdjacencyGraph, TopologyService]:
        """Convenience loader initializing homography manager, camera graph, and topology service."""
        calib_dir = _resolve_data_path(calibration_dir or "data/calibration", "data/calibration")
        top_dir = _resolve_data_path(topology_dir or "data/topology", "data/topology")

        cameras = cls.load_cameras(calibration_dir=str(calib_dir))
        homography_mgr = cls.load_homographies(
            calibration_dir=str(calib_dir),
            geometry_version=geometry_version,
        )
        graph = cls.load_topology(
            topology_dir=str(top_dir),
            cameras=cameras,
        )
        # Verify graph connectivity across all cameras
        graph.validate_connectivity(require_connected=True)

        topology_service = TopologyService(graph=graph)

        return homography_mgr, graph, topology_service
