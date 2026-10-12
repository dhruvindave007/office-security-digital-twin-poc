import logging
import os
from pathlib import Path
import threading
from typing import Dict, List, Optional, Tuple, Union
import numpy as np

try:
    from vision.geometry import FloorPoint, PixelPoint, ProjectionResult
    from vision.validators import CalibrationValidator, CameraValidator
except ImportError:
    from src.vision.geometry import FloorPoint, PixelPoint, ProjectionResult
    from src.vision.validators import CalibrationValidator, CameraValidator

logger = logging.getLogger("office_vision.vision.calibration")


class HomographyManager:
    """Manages planar homography transformations between camera pixel coordinates and office floor coordinates.

    Key capabilities:
    1. Validates 3x3 non-singular, well-conditioned homography matrices.
    2. Automatically computes and caches inverse matrices (inv(H)) upon registration.
    3. Provides forward (pixel -> floor) and inverse (floor -> pixel) projections.
    4. Tracks geometry versioning per camera.
    5. Thread-safe internal storage.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._homographies: Dict[str, np.ndarray] = {}
        self._inverse_cache: Dict[str, np.ndarray] = {}
        self._geometry_versions: Dict[str, int] = {}
        self._inversion_hit_count: int = 0
        self._inversion_calc_count: int = 0

    def register_homography(
        self,
        camera_id: str,
        matrix: np.ndarray,
        geometry_version: int = 1,
    ) -> None:
        """Validate, invert, and register a 3x3 homography matrix for a camera.

        Precomputes and caches the inverse matrix immediately.
        """
        camera_id = CameraValidator.validate_camera_id(camera_id)
        valid_matrix = CalibrationValidator.validate_homography_matrix(matrix)

        with self._lock:
            # Precompute inverse and cache
            inv_matrix = np.linalg.inv(valid_matrix).astype(np.float64)
            self._inversion_calc_count += 1

            self._homographies[camera_id] = valid_matrix
            self._inverse_cache[camera_id] = inv_matrix
            self._geometry_versions[camera_id] = int(geometry_version)

        logger.debug(
            "Registered homography for camera '%s' (geometry_version=%d, cond=%.2f)",
            camera_id,
            geometry_version,
            float(np.linalg.cond(valid_matrix)),
        )

    def load_homography_file(
        self,
        camera_id: str,
        file_path: Union[str, Path],
        geometry_version: int = 1,
    ) -> None:
        """Load a 3x3 homography from a .npy file and register it."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Homography file not found for camera '{camera_id}' at: {path}")

        try:
            matrix = np.load(str(path))
        except Exception as e:
            raise ValueError(f"Failed to load numpy matrix from '{path}': {e}") from e

        self.register_homography(camera_id, matrix, geometry_version=geometry_version)

    def project_pixel_to_floor(
        self,
        camera_id: str,
        pixel_point: PixelPoint,
        confidence: float = 1.0,
    ) -> ProjectionResult:
        """Forward projection: convert camera pixel (u, v) into floor coordinates (x, y).

        Homogeneous equation:
            [s*x, s*y, s]^T = H * [u, v, 1]^T
            x = (s*x) / s
            y = (s*y) / s

        Raises:
            KeyError: If camera_id has no registered homography.
            ValueError: If the projection produces a degenerate scale factor (s ~ 0).
        """
        with self._lock:
            if camera_id not in self._homographies:
                raise KeyError(f"No homography calibration registered for camera '{camera_id}'.")
            H = self._homographies[camera_id]
            geom_ver = self._geometry_versions.get(camera_id, 1)

        vec = np.array([pixel_point.u, pixel_point.v, 1.0], dtype=np.float64)
        proj = H @ vec

        scale = proj[2]
        if abs(scale) < 1e-12:
            raise ValueError(
                f"Degenerate projection for camera '{camera_id}' at pixel ({pixel_point.u}, {pixel_point.v}): scale={scale}."
            )

        x = float(proj[0] / scale)
        y = float(proj[1] / scale)

        return ProjectionResult(
            floor_point=FloorPoint(x=x, y=y),
            pixel_point=pixel_point,
            camera_id=camera_id,
            confidence=confidence,
            geometry_version=geom_ver,
        )

    def project_floor_to_pixel(
        self,
        camera_id: str,
        floor_point: FloorPoint,
    ) -> PixelPoint:
        """Inverse projection: convert floor coordinate (x, y) back into camera pixel (u, v).

        Uses precomputed cached inverse matrix H^-1:
            [s'*u, s'*v, s']^T = H^-1 * [x, y, 1]^T
            u = (s'*u) / s'
            v = (s'*v) / s'

        Raises:
            KeyError: If camera_id has no registered homography.
            ValueError: If the projection produces a degenerate scale factor (s' ~ 0).
        """
        with self._lock:
            if camera_id not in self._inverse_cache:
                raise KeyError(f"No inverse homography cached for camera '{camera_id}'.")
            inv_H = self._inverse_cache[camera_id]
            self._inversion_hit_count += 1

        vec = np.array([floor_point.x, floor_point.y, 1.0], dtype=np.float64)
        proj = inv_H @ vec

        scale = proj[2]
        if abs(scale) < 1e-12:
            raise ValueError(
                f"Degenerate inverse projection for camera '{camera_id}' at floor ({floor_point.x}, {floor_point.y}): scale={scale}."
            )

        u = float(proj[0] / scale)
        v = float(proj[1] / scale)

        # Enforce non-negative pixel coordinates
        return PixelPoint(u=max(0.0, u), v=max(0.0, v))

    def batch_project_pixel_to_floor(
        self,
        camera_id: str,
        pixel_points: List[PixelPoint],
    ) -> List[FloorPoint]:
        """Vectorized batch forward projection."""
        if not pixel_points:
            return []

        with self._lock:
            if camera_id not in self._homographies:
                raise KeyError(f"No homography calibration registered for camera '{camera_id}'.")
            H = self._homographies[camera_id]

        pts = np.array([[p.u, p.v, 1.0] for p in pixel_points], dtype=np.float64).T
        proj = H @ pts  # (3, N)

        scales = proj[2, :]
        if np.any(np.abs(scales) < 1e-12):
            raise ValueError(f"Batch projection contains points with near-zero scale factor for camera '{camera_id}'.")

        xs = proj[0, :] / scales
        ys = proj[1, :] / scales

        return [FloorPoint(x=float(x), y=float(y)) for x, y in zip(xs, ys)]

    def has_camera(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._homographies

    def get_homography(self, camera_id: str) -> np.ndarray:
        with self._lock:
            if camera_id not in self._homographies:
                raise KeyError(f"Camera '{camera_id}' not found.")
            return np.copy(self._homographies[camera_id])

    def get_inverse_homography(self, camera_id: str) -> np.ndarray:
        with self._lock:
            if camera_id not in self._inverse_cache:
                raise KeyError(f"Camera '{camera_id}' not found.")
            self._inversion_hit_count += 1
            return np.copy(self._inverse_cache[camera_id])

    def get_geometry_version(self, camera_id: str) -> int:
        with self._lock:
            return self._geometry_versions.get(camera_id, 1)

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "registered_cameras": len(self._homographies),
                "inversions_calculated": self._inversion_calc_count,
                "inversion_cache_hits": self._inversion_hit_count,
            }

    def clear(self) -> None:
        with self._lock:
            self._homographies.clear()
            self._inverse_cache.clear()
            self._geometry_versions.clear()
            self._inversion_hit_count = 0
            self._inversion_calc_count = 0
