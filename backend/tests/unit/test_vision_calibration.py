import math
import tempfile
from pathlib import Path
import numpy as np
import pytest

from src.vision.calibration import HomographyManager
from src.vision.geometry import FloorPoint, PixelPoint


def _compute_test_homography(src_pts, dst_pts):
    """Compute exact 3x3 homography from 4 point correspondences."""
    A = []
    for (u, v), (x, y) in zip(src_pts, dst_pts):
        A.append([-u, -v, -1, 0, 0, 0, u * x, v * x, x])
        A.append([0, 0, 0, -u, -v, -1, u * y, v * y, y])
    A = np.array(A, dtype=np.float64)
    _, _, Vt = np.linalg.svd(A)
    H = Vt[-1].reshape((3, 3))
    return H / H[2, 2]


def test_homography_forward_and_inverse():
    """Verify forward and inverse projection with round-trip fidelity."""
    mgr = HomographyManager()
    src = [(0.0, 0.0), (1920.0, 0.0), (1920.0, 1080.0), (0.0, 1080.0)]
    dst = [(1.0, 1.0), (11.0, 1.5), (10.0, 9.0), (1.5, 8.5)]
    H = _compute_test_homography(src, dst)

    mgr.register_homography("cam_test", H, geometry_version=2)

    # 1. Forward projection of center pixel
    pix_center = PixelPoint(u=960.0, v=540.0)
    res = mgr.project_pixel_to_floor("cam_test", pix_center)
    assert isinstance(res.floor_point, FloorPoint)
    assert res.geometry_version == 2
    assert 1.0 < res.floor_point.x < 11.0
    assert 1.0 < res.floor_point.y < 9.0

    # 2. Inverse projection of projected floor coordinate
    recovered_pixel = mgr.project_floor_to_pixel("cam_test", res.floor_point)

    # 3. Round-trip fidelity check (< 1e-5 pixels error)
    diff = math.hypot(recovered_pixel.u - pix_center.u, recovered_pixel.v - pix_center.v)
    assert diff < 1e-5


def test_cached_inverse_homography():
    """Verify that inverse homography is cached on registration and reused without recomputation."""
    mgr = HomographyManager()
    eye = np.eye(3, dtype=np.float64)
    mgr.register_homography("cam_eye", eye)

    stats = mgr.get_stats()
    assert stats["inversions_calculated"] == 1
    assert stats["inversion_cache_hits"] == 0

    # Call project_floor_to_pixel multiple times
    mgr.project_floor_to_pixel("cam_eye", FloorPoint(x=5.0, y=5.0))
    mgr.project_floor_to_pixel("cam_eye", FloorPoint(x=10.0, y=10.0))

    stats2 = mgr.get_stats()
    assert stats2["inversions_calculated"] == 1  # No re-inversion
    assert stats2["inversion_cache_hits"] == 2  # Cache hits recorded


def test_batch_projection():
    """Verify batch_project_pixel_to_floor produces identical results to sequential projections."""
    mgr = HomographyManager()
    src = [(0.0, 0.0), (1920.0, 0.0), (1920.0, 1080.0), (0.0, 1080.0)]
    dst = [(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0)]
    H = _compute_test_homography(src, dst)
    mgr.register_homography("cam_batch", H)

    pixels = [
        PixelPoint(u=100.0, v=100.0),
        PixelPoint(u=500.0, v=500.0),
        PixelPoint(u=960.0, v=540.0),
        PixelPoint(u=1800.0, v=1000.0),
    ]

    single_results = [mgr.project_pixel_to_floor("cam_batch", p).floor_point for p in pixels]
    batch_results = mgr.batch_project_pixel_to_floor("cam_batch", pixels)

    assert len(single_results) == len(batch_results)
    for s, b in zip(single_results, batch_results):
        assert math.isclose(s.x, b.x, abs_tol=1e-6)
        assert math.isclose(s.y, b.y, abs_tol=1e-6)


def test_unregistered_camera_error():
    """Verify KeyError is raised when projecting through an unregistered camera."""
    mgr = HomographyManager()
    with pytest.raises(KeyError, match="No homography"):
        mgr.project_pixel_to_floor("non_existent_cam", PixelPoint(100.0, 100.0))

    with pytest.raises(KeyError, match="No inverse homography"):
        mgr.project_floor_to_pixel("non_existent_cam", FloorPoint(5.0, 5.0))


def test_load_homography_file():
    """Verify loading from .npy file on disk."""
    mgr = HomographyManager()
    H = np.eye(3, dtype=np.float64) * 2.0
    H[2, 2] = 1.0

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "cam_temp.npy"
        np.save(str(file_path), H)

        mgr.load_homography_file("cam_temp", file_path, geometry_version=3)
        assert mgr.has_camera("cam_temp")
        assert mgr.get_geometry_version("cam_temp") == 3

        proj = mgr.project_pixel_to_floor("cam_temp", PixelPoint(u=10.0, v=20.0))
        assert proj.floor_point.x == 20.0
        assert proj.floor_point.y == 40.0


def test_missing_homography_file():
    """Verify FileNotFoundError when loading non-existent homography file."""
    mgr = HomographyManager()
    with pytest.raises(FileNotFoundError):
        mgr.load_homography_file("cam_missing", "data/non_existent.npy")
