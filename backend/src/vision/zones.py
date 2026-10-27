# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import logging
from typing import Dict, List, Optional, Tuple
from shapely import prepare
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

try:
    from twin.models import Zone
    from twin.store import TwinStore
    from vision.geometry import FloorPoint
except ImportError:
    from src.twin.models import Zone
    from src.twin.store import TwinStore
    from src.vision.geometry import FloorPoint

logger = logging.getLogger("office_vision.vision.zones")


class VisionZoneResolver:
    """Bridges vision floor projections to Digital Twin spatial zones.

    Key capabilities:
    1. Pre-compiles 2D zone polygons with shapely.prepare() for high-speed C/GEOS point-in-polygon testing.
    2. Builds a Shapely 2.0 STRtree spatial index for logarithmic candidate pruning.
    3. Resolves overlapping boundary candidates deterministically by security_level DESC.
    4. Enforces geometry_version tracking.
    """

    def __init__(
        self,
        zones: List[Zone],
        vertices_map: Dict[str, List[Tuple[float, float]]],
    ):
        self._zones: Dict[str, Zone] = {z.zone_id: z for z in zones}
        self._polygons: List[Polygon] = []
        self._zone_list: List[Zone] = []
        self._active_geometry_version: int = 1

        for zone in zones:
            coords = vertices_map.get(zone.zone_id, [])
            if len(coords) >= 3:
                # Strip duplicate closing point if present
                clean_coords = list(coords)
                if len(clean_coords) > 3 and clean_coords[0] == clean_coords[-1]:
                    clean_coords = clean_coords[:-1]

                poly = Polygon(clean_coords)
                if poly.is_valid and poly.area > 0:
                    prepare(poly)
                    self._polygons.append(poly)
                    self._zone_list.append(zone)
                    if zone.geometry_version > self._active_geometry_version:
                        self._active_geometry_version = zone.geometry_version

        if self._polygons:
            self._tree = STRtree(self._polygons)
        else:
            self._tree = None

        logger.info(
            "Initialized VisionZoneResolver with %d active zone polygons (active_geometry_version=%d).",
            len(self._polygons),
            self._active_geometry_version,
        )

    @classmethod
    def from_twin_store(cls, store: TwinStore) -> "VisionZoneResolver":
        """Initialize directly from an active TwinStore repository."""
        zones = store.get_zones()
        vertices_map: Dict[str, List[Tuple[float, float]]] = {}
        for z in zones:
            raw_verts = store.get_zone_vertices(z.zone_id)
            vertices_map[z.zone_id] = [(v.x, v.y) for v in raw_verts]
        return cls(zones=zones, vertices_map=vertices_map)

    def resolve_zone(
        self,
        floor_point: FloorPoint,
        geometry_version: Optional[int] = None,
    ) -> Optional[str]:
        """Resolve which security zone contains the specified floor coordinate.

        Returns matching zone_id, or None if point is in unzoned space.
        If overlapping zones match, resolves by highest security_level.
        """
        if self._tree is None or not self._polygons:
            return None

        if geometry_version is not None and geometry_version != self._active_geometry_version:
            logger.debug(
                "Observation geometry_version=%d does not match active_geometry_version=%d.",
                geometry_version,
                self._active_geometry_version,
            )

        pt = Point(floor_point.x, floor_point.y)
        # STRtree returns indices into self._polygons
        candidates = self._tree.query(pt)

        matching_zones: List[Zone] = []
        for idx in candidates:
            poly = self._polygons[idx]
            if poly.contains(pt) or poly.touches(pt):
                matching_zones.append(self._zone_list[idx])

        if not matching_zones:
            return None

        # Sort descending by security_level for deterministic tie-breaking
        matching_zones.sort(key=lambda z: z.security_level, reverse=True)
        return matching_zones[0].zone_id

    def get_zone(self, zone_id: str) -> Optional[Zone]:
        return self._zones.get(zone_id)

    def get_all_zones(self) -> List[Zone]:
        return list(self._zones.values())

    @property
    def active_geometry_version(self) -> int:
        return self._active_geometry_version
