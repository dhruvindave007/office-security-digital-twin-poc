# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import math
import re
from typing import Optional, Tuple
import numpy as np

try:
    from vision.geometry import PixelPoint
except ImportError:
    from src.vision.geometry import PixelPoint


class CalibrationValidator:
    """Validates 3x3 homography projection matrices."""

    MAX_CONDITION_NUMBER = 1e8
    MIN_DETERMINANT = 1e-7

    @classmethod
    def validate_homography_matrix(cls, matrix: np.ndarray) -> np.ndarray:
        """Validate that a numpy array is a valid, non-singular 3x3 homography matrix.

        Raises:
            ValueError: If shape != (3, 3), contains NaNs/Infs, determinant ~ 0,
                        or condition number indicates severe numerical degeneracy.
        """
        if not isinstance(matrix, np.ndarray):
            try:
                matrix = np.array(matrix, dtype=np.float64)
            except Exception as e:
                raise ValueError(f"Matrix must be a numpy array or array-like: {e}")

        if matrix.shape != (3, 3):
            raise ValueError(f"Homography matrix must have shape (3, 3), got {matrix.shape}.")

        if not np.all(np.isfinite(matrix)):
            raise ValueError("Homography matrix contains non-finite values (NaN or Inf).")

        det = float(np.linalg.det(matrix))
        if abs(det) < cls.MIN_DETERMINANT:
            raise ValueError(
                f"Homography matrix is singular or near-singular: determinant={det} (< {cls.MIN_DETERMINANT})."
            )

        cond = float(np.linalg.cond(matrix))
        if cond > cls.MAX_CONDITION_NUMBER:
            raise ValueError(
                f"Homography matrix condition number {cond:.2e} exceeds stability threshold {cls.MAX_CONDITION_NUMBER:.2e}."
            )

        return matrix.astype(np.float64)


class CameraValidator:
    """Validates camera identifiers and hardware specifications."""

    CAMERA_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")

    @classmethod
    def validate_camera_id(cls, camera_id: str) -> str:
        if not camera_id or not isinstance(camera_id, str):
            raise ValueError("camera_id must be a non-empty string.")
        cleaned = camera_id.strip()
        if not cleaned:
            raise ValueError("camera_id cannot be blank.")
        if not cls.CAMERA_ID_REGEX.match(cleaned):
            raise ValueError(f"camera_id '{cleaned}' contains invalid characters. Must match [a-zA-Z0-9_-]+.")
        return cleaned

    @classmethod
    def validate_resolution(cls, width: int, height: int) -> Tuple[int, int]:
        if not isinstance(width, int) or not isinstance(height, int):
            raise ValueError("Resolution width and height must be integers.")
        if width < 320 or height < 240:
            raise ValueError(f"Resolution ({width}x{height}) is smaller than minimum supported (320x240).")
        return width, height

    @classmethod
    def validate_fps(cls, fps: float) -> float:
        if not math.isfinite(fps) or fps <= 0.0 or fps > 240.0:
            raise ValueError(f"FPS must be a strictly positive finite number in (0, 240], got {fps}.")
        return float(fps)


class TopologyValidator:
    """Validates camera graph edge properties and transit timing."""

    @classmethod
    def validate_transit_range(cls, min_seconds: float, max_seconds: float) -> Tuple[float, float]:
        if not math.isfinite(min_seconds) or not math.isfinite(max_seconds):
            raise ValueError(f"Transit timing must be finite: min={min_seconds}, max={max_seconds}.")
        if min_seconds <= 0.0:
            raise ValueError(f"Minimum transit time must be strictly positive (> 0.0), got {min_seconds}.")
        if max_seconds < min_seconds:
            raise ValueError(
                f"Maximum transit time ({max_seconds}s) cannot be less than minimum transit time ({min_seconds}s)."
            )
        return float(min_seconds), float(max_seconds)

    @classmethod
    def validate_distance(cls, distance_meters: float) -> float:
        if not math.isfinite(distance_meters) or distance_meters < 0.0:
            raise ValueError(f"Distance meters must be a non-negative finite number, got {distance_meters}.")
        return float(distance_meters)


class ProjectionValidator:
    """Validates pixel space bounds and coordinates."""

    @classmethod
    def validate_pixel_bounds(
        cls,
        pixel: PixelPoint,
        width: int,
        height: int,
        margin: float = 100.0,
    ) -> None:
        """Check if pixel point is reasonably within camera image bounds (with optional margin)."""
        if pixel.u < -margin or pixel.u > width + margin:
            raise ValueError(f"Pixel u={pixel.u} is outside valid camera image width ({width}) with margin {margin}.")
        if pixel.v < -margin or pixel.v > height + margin:
            raise ValueError(f"Pixel v={pixel.v} is outside valid camera image height ({height}) with margin {margin}.")
