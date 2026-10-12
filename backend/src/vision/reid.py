from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
from typing import Any, List, Sequence, Tuple


@dataclass(frozen=True)
class VisualFeatureVector:
    """Represents a normalized visual embedding vector extracted from a person detection crop."""
    embedding: Tuple[float, ...]
    camera_id: str
    track_id: str
    timestamp: str

    def __post_init__(self):
        if not self.embedding:
            raise ValueError("VisualFeatureVector embedding cannot be empty.")
        norm = math.sqrt(sum(x * x for x in self.embedding))
        if norm <= 0.0 or not math.isfinite(norm):
            raise ValueError("VisualFeatureVector embedding must have a positive finite L2 norm.")

    @property
    def dimension(self) -> int:
        return len(self.embedding)

    @property
    def l2_norm(self) -> float:
        return math.sqrt(sum(x * x for x in self.embedding))


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute cosine similarity between two feature vectors with zero-division protection.

    Returns value in [-1.0, 1.0].
    """
    if len(vec_a) != len(vec_b):
        raise ValueError(f"Vector dimensions must match: {len(vec_a)} vs {len(vec_b)}.")

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    sim = dot_product / (norm_a * norm_b)
    return max(-1.0, min(1.0, float(sim)))


class ReIDExtractorInterface(ABC):
    """Abstract interface for local person Re-Identification feature extraction."""

    @abstractmethod
    def extract_feature(self, image_crop: Any, camera_id: str, track_id: str, timestamp: str) -> VisualFeatureVector:
        """Extract a normalized visual embedding from a cropped person bounding box image."""
        pass


class ReIDMatcherInterface(ABC):
    """Abstract interface for matching query features against gallery embeddings."""

    @abstractmethod
    def compute_similarity(self, feat_a: VisualFeatureVector, feat_b: VisualFeatureVector) -> float:
        """Compute visual similarity score between two feature vectors."""
        pass

    @abstractmethod
    def match(
        self,
        query: VisualFeatureVector,
        gallery: List[VisualFeatureVector],
        threshold: float = 0.7,
    ) -> List[Tuple[VisualFeatureVector, float]]:
        """Find matches in gallery with similarity score above threshold, sorted descending."""
        pass
