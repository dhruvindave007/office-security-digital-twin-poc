from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TokenUsage:
    """Canonical normalized token consumption metrics.

    Normalizes provider-specific usage metrics into a unified structure.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None


@dataclass(frozen=True)
class ModelCapabilities:
    """Declared capabilities for a foundation model or inference profile."""
    supports_converse: bool = True
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_multimodal: bool = False
    supports_video: bool = False
    max_context_tokens: int = 128_000
    description: str = ""


@dataclass(frozen=True)
class VerifiedModelCapabilities:
    """Validated model capabilities with source attribution and timestamp."""
    model_id: str
    supports_converse: bool = True
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_multimodal: bool = False
    supports_video: bool = False
    max_context_tokens: int = 128_000
    source: str = "static_catalog"  # 'dynamic_aws' or 'static_catalog'
    verified_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass(frozen=True)
class BedrockConverseResponse:
    """Standardized response contract for all Bedrock invocations."""
    text: str
    model_id: str
    region: str
    request_id: str
    latency_ms: float
    usage: TokenUsage
    stop_reason: str
    streamed: bool = False
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    content_blocks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class BedrockStreamChunk:
    """Incremental chunk yielded during ConverseStream execution."""
    delta_text: str
    is_final: bool = False
    stop_reason: Optional[str] = None
    accumulated_text: str = ""
    response: Optional[BedrockConverseResponse] = None
