from dataclasses import FrozenInstanceError
import math
from typing import List, Tuple
import pytest

from src.vision.reid import (
    ReIDMatcherInterface,
    VisualFeatureVector,
    cosine_similarity,
)


def test_reid_feature_vector_immutability_and_validation():
    """Verify VisualFeatureVector properties, L2 norm, and immutability."""
    vec = VisualFeatureVector(
        embedding=(1.0, 2.0, 2.0),
        camera_id="cam_01",
        track_id="trk_1",
        timestamp="2026-09-19T10:00:00Z",
    )
    assert vec.dimension == 3
    assert math.isclose(vec.l2_norm, 3.0)  # sqrt(1 + 4 + 4) = 3.0

    with pytest.raises(FrozenInstanceError):
        vec.camera_id = "cam_02"  # type: ignore

    # Empty embedding rejected
    with pytest.raises(ValueError, match="cannot be empty"):
        VisualFeatureVector(
            embedding=(),
            camera_id="cam_01",
            track_id="trk_1",
            timestamp="2026-09-19T10:00:00Z",
        )

    # Zero norm rejected
    with pytest.raises(ValueError, match="positive finite"):
        VisualFeatureVector(
            embedding=(0.0, 0.0, 0.0),
            camera_id="cam_01",
            track_id="trk_1",
            timestamp="2026-09-19T10:00:00Z",
        )


def test_reid_cosine_similarity():
    """Verify cosine_similarity mathematical properties."""
    # Identical vectors -> 1.0
    v1 = (1.0, 0.0, 0.0)
    assert math.isclose(cosine_similarity(v1, v1), 1.0)

    # Orthogonal vectors -> 0.0
    v2 = (0.0, 1.0, 0.0)
    assert math.isclose(cosine_similarity(v1, v2), 0.0)

    # Opposite vectors -> -1.0
    v3 = (-1.0, 0.0, 0.0)
    assert math.isclose(cosine_similarity(v1, v3), -1.0)

    # General vectors
    a = (1.0, 2.0, 3.0)
    b = (2.0, 4.0, 6.0)
    assert math.isclose(cosine_similarity(a, b), 1.0)

    # Dimension mismatch
    with pytest.raises(ValueError, match="dimensions must match"):
        cosine_similarity((1.0, 2.0), (1.0, 2.0, 3.0))


def test_reid_matcher_mock():
    """Verify mock implementation of ReIDMatcherInterface against gallery."""

    class MockMatcher(ReIDMatcherInterface):
        def compute_similarity(self, feat_a: VisualFeatureVector, feat_b: VisualFeatureVector) -> float:
            return cosine_similarity(feat_a.embedding, feat_b.embedding)

        def match(
            self,
            query: VisualFeatureVector,
            gallery: List[VisualFeatureVector],
            threshold: float = 0.7,
        ) -> List[Tuple[VisualFeatureVector, float]]:
            matches = []
            for item in gallery:
                score = self.compute_similarity(query, item)
                if score >= threshold:
                    matches.append((item, score))
            matches.sort(key=lambda x: x[1], reverse=True)
            return matches

    matcher = MockMatcher()
    query = VisualFeatureVector((1.0, 0.0, 0.0), "cam_1", "trk_q", "2026-09-19T10:00:00Z")
    g1 = VisualFeatureVector((0.9, 0.1, 0.0), "cam_2", "trk_1", "2026-09-19T10:00:05Z")
    g2 = VisualFeatureVector((0.0, 1.0, 0.0), "cam_2", "trk_2", "2026-09-19T10:00:06Z")

    results = matcher.match(query, [g1, g2], threshold=0.8)
    assert len(results) == 1
    assert results[0][0].track_id == "trk_1"
    assert results[0][1] > 0.8
