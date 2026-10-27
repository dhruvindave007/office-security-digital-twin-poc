# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import numpy as np
import pytest

from src.vision.geometry import PixelPoint
from src.vision.validators import (
    CalibrationValidator,
    CameraValidator,
    ProjectionValidator,
    TopologyValidator,
)


def test_calibration_validator_valid_matrix():
    """Verify CalibrationValidator accepts valid 3x3 matrices."""
    eye = np.eye(3, dtype=np.float64)
    res = CalibrationValidator.validate_homography_matrix(eye)
    assert res.shape == (3, 3)

    affine = np.array([[2.0, 0.5, 10.0], [-0.2, 1.8, 5.0], [0.0, 0.0, 1.0]])
    res2 = CalibrationValidator.validate_homography_matrix(affine)
    assert res2.shape == (3, 3)


def test_calibration_validator_invalid_shape():
    """Verify CalibrationValidator rejects matrices that are not 3x3."""
    with pytest.raises(ValueError, match=r"shape \(3, 3\)"):
        CalibrationValidator.validate_homography_matrix(np.eye(2))

    with pytest.raises(ValueError, match=r"shape \(3, 3\)"):
        CalibrationValidator.validate_homography_matrix(np.zeros((3, 4)))


def test_calibration_validator_non_finite():
    """Verify CalibrationValidator rejects matrices with NaNs or Infs."""
    nan_mat = np.eye(3)
    nan_mat[0, 1] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        CalibrationValidator.validate_homography_matrix(nan_mat)

    inf_mat = np.eye(3)
    inf_mat[2, 2] = np.inf
    with pytest.raises(ValueError, match="non-finite"):
        CalibrationValidator.validate_homography_matrix(inf_mat)


def test_calibration_validator_singular():
    """Verify CalibrationValidator rejects singular matrices (zero determinant)."""
    singular = np.array([[1.0, 2.0, 3.0], [2.0, 4.0, 6.0], [0.0, 0.0, 1.0]])  # Row 2 is 2x Row 1
    with pytest.raises(ValueError, match="singular"):
        CalibrationValidator.validate_homography_matrix(singular)


def test_calibration_validator_ill_conditioned():
    """Verify CalibrationValidator rejects severely ill-conditioned matrices."""
    ill_cond = np.array([[1e9, 0.0, 0.0], [0.0, 1e-3, 0.0], [0.0, 0.0, 1.0]])
    with pytest.raises(ValueError, match="condition number"):
        CalibrationValidator.validate_homography_matrix(ill_cond)


def test_camera_validator_id():
    """Verify CameraValidator checks ID pattern and non-emptiness."""
    assert CameraValidator.validate_camera_id("cam_01") == "cam_01"
    assert CameraValidator.validate_camera_id("CAM-RECEPTION-02") == "CAM-RECEPTION-02"

    with pytest.raises(ValueError, match="blank"):
        CameraValidator.validate_camera_id("   ")

    with pytest.raises(ValueError, match="invalid characters"):
        CameraValidator.validate_camera_id("cam@door!01")


def test_camera_validator_resolution_and_fps():
    """Verify CameraValidator checks resolution boundaries and FPS."""
    w, h = CameraValidator.validate_resolution(1920, 1080)
    assert (w, h) == (1920, 1080)

    with pytest.raises(ValueError, match="smaller than minimum"):
        CameraValidator.validate_resolution(200, 100)

    fps = CameraValidator.validate_fps(30.0)
    assert fps == 30.0

    with pytest.raises(ValueError, match="strictly positive"):
        CameraValidator.validate_fps(0.0)

    with pytest.raises(ValueError, match="strictly positive"):
        CameraValidator.validate_fps(-15.0)


def test_topology_validator_transit():
    """Verify TopologyValidator enforces positive and ordered transit ranges."""
    t_min, t_max = TopologyValidator.validate_transit_range(5.0, 20.0)
    assert (t_min, t_max) == (5.0, 20.0)

    with pytest.raises(ValueError, match="strictly positive"):
        TopologyValidator.validate_transit_range(0.0, 10.0)

    with pytest.raises(ValueError, match="cannot be less than minimum"):
        TopologyValidator.validate_transit_range(15.0, 10.0)


def test_topology_validator_distance():
    """Verify TopologyValidator enforces non-negative distance."""
    assert TopologyValidator.validate_distance(12.5) == 12.5
    assert TopologyValidator.validate_distance(0.0) == 0.0

    with pytest.raises(ValueError, match="non-negative"):
        TopologyValidator.validate_distance(-1.0)


def test_projection_validator():
    """Verify ProjectionValidator checks pixel coordinates against image dimensions with margin."""
    ProjectionValidator.validate_pixel_bounds(PixelPoint(100.0, 200.0), 1920, 1080)
    # Inside margin (e.g. 1950 is within 1920 + 100)
    ProjectionValidator.validate_pixel_bounds(PixelPoint(1950.0, 1000.0), 1920, 1080, margin=100.0)

    with pytest.raises(ValueError, match="outside valid"):
        ProjectionValidator.validate_pixel_bounds(PixelPoint(2500.0, 500.0), 1920, 1080, margin=100.0)
