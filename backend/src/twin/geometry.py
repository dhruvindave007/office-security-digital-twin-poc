# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import logging
from typing import Dict, List, Optional, Tuple

from shapely import prepare
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree
from src.twin.models import Zone
from src.twin.validators import PolygonValidator

logger = logging.getLogger("office_vision.twin.geometry")


class GeometryEngine:
    """High-performance 2D spatial query engine using Shapely 2.0 STRtree and PreparedGeometry.

    Combines R-tree spatial indexing for O(log N) bounding-box candidate pruning with
    compiled C/GEOS PreparedGeometry containment predicates and security-level priority resolution.
    """

    def __init__(
        self,
        zones: List[Zone],
        vertices_by_zone: Dict[str, List[Tuple[float, float]]],
    ):
        self._zones: List[Zone] = []
        self._polygons: List[Polygon] = []
        self._zone_by_id: Dict[str, Zone] = {}
        self._poly_by_id: Dict[str, Polygon] = {}
        self._geometry_version: int = 1

        for zone in zones:
            coords = vertices_by_zone.get(zone.zone_id, [])
            if not coords:
                logger.warning("Zone '%s' has no vertices defined; skipping spatial index.", zone.zone_id)
                continue

            # Validate polygon geometry
            result = PolygonValidator.validate_vertices(coords)
            poly = result.polygon

            # Update zone bounding box and track geometry version
            zone.min_x = result.min_x
            zone.min_y = result.min_y
            zone.max_x = result.max_x
            zone.max_y = result.max_y
            self._geometry_version = max(self._geometry_version, zone.geometry_version)

            # In-place GEOS preparation for accelerated containment tests
            prepare(poly)

            self._zones.append(zone)
            self._polygons.append(poly)
            self._zone_by_id[zone.zone_id] = zone
            self._poly_by_id[zone.zone_id] = poly

        # Build STRtree spatial index over all zone boundary polygons
        if self._polygons:
            self._tree = STRtree(self._polygons)
        else:
            self._tree = None

    @property
    def geometry_version(self) -> int:
        """Return the current geometry version of the indexed floorplan."""
        return self._geometry_version

    def find_zone(self, x: float, y: float) -> Optional[str]:
        """Resolve the primary security zone containing the 2D planar point (x, y).

        If multiple zones overlap at the coordinate, the zone with the highest
        security_level takes precedence.

        Returns:
            zone_id if point resides inside an indexed zone, else None.
        """
        if not self._tree or not self._polygons:
            return None

        pt = Point(x, y)
        candidate_indices = self._tree.query(pt)
        if len(candidate_indices) == 0:
            return None

        matching_zones: List[Tuple[int, str]] = []
        for idx in candidate_indices:
            poly = self._polygons[idx]
            # Fast prepared point-in-polygon containment check
            if poly.contains(pt) or poly.touches(pt):
                zone = self._zones[idx]
                matching_zones.append((zone.security_level, zone.zone_id))

        if not matching_zones:
            return None

        # Priority tie-breaking: highest security_level wins
        matching_zones.sort(key=lambda t: t[0], reverse=True)
        return matching_zones[0][1]

    def find_all_zones(self, x: float, y: float) -> List[str]:
        """Return all zones whose boundaries encompass the given coordinate."""
        if not self._tree:
            return []

        pt = Point(x, y)
        candidate_indices = self._tree.query(pt)
        matches: List[str] = []
        for idx in candidate_indices:
            if self._polygons[idx].contains(pt) or self._polygons[idx].touches(pt):
                matches.append(self._zones[idx].zone_id)
        return matches

    def get_zone_polygon(self, zone_id: str) -> Optional[Polygon]:
        """Fetch the Shapely Polygon geometry for a given zone."""
        return self._poly_by_id.get(zone_id)
