"""Image box -> floor position, per camera.

Two calibration modes (data/sites/<site>/calibration.json):
  metric: a pixel->floor homography; the person's feet (bottom-centre of the box) map to metres.
          Feet above `trusted_min_foot_v` (far away, where lens distortion dominates) fall back to room level.
  room:   the camera only tells us WHICH room the person is in; they are placed at the room's centre point.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class SiteProjector:
    def __init__(self, calibration: Dict, room_points: Dict[str, Tuple[float, float]]):
        self.cameras = calibration["cameras"]
        self.room_points = room_points
        self._H = {}
        for cam, cc in self.cameras.items():
            if cc.get("mode") == "metric":
                import numpy as np
                self._H[cam] = np.array(cc["pixel_to_floor_H"], dtype=float)

    @classmethod
    def from_files(cls, calibration_path: str, store) -> "SiteProjector":
        from shapely.geometry import Polygon
        cal = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
        pts = {z: Polygon([(p.x, p.y) for p in vs]).representative_point().coords[0]
               for z, vs in store.get_all_vertices_by_zone().items() if len(vs) >= 3}
        return cls(cal, pts)

    def knows(self, camera_id: str) -> bool:
        return camera_id in self.cameras

    def project(self, camera_id: str, box_xywh: List[float], img_wh: Tuple[int, int]) -> Optional[Tuple[float, float, str]]:
        """(x, y, mode) in metres, or None when the camera is unknown."""
        cc = self.cameras.get(camera_id)
        if cc is None:
            return None
        x, y, w, h = box_xywh
        u, v = x + w / 2.0, y + h
        if camera_id in self._H:
            import cv2
            import numpy as np
            sx, sy = cc["image_size"][0] / img_wh[0], cc["image_size"][1] / img_wh[1]
            u, v = u * sx, v * sy
            if cc.get("trusted_min_foot_v", 0) <= v < cc["image_size"][1] - 5:
                p = cv2.perspectiveTransform(np.float32([[[u, v]]]), self._H[camera_id])[0, 0]
                return float(p[0]), float(p[1]), "metric"
        zp = self.room_points.get(cc.get("zone_id"))
        if zp is None:
            return None
        return float(zp[0]), float(zp[1]), "room"
