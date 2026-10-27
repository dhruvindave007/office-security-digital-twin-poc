# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""AI package for Office Vision Intelligence Platform.

Provides integration with Amazon Bedrock Runtime via the Converse API,
dynamic model capability validation, conversation management, and telemetry.
"""

from src.ai.bedrock_client import BedrockClient
from src.ai.conversation import ConversationManager
from src.ai.errors import (
    BedrockAccessDeniedError,
    BedrockCredentialsError,
    BedrockError,
    BedrockModelNotFoundError,
    BedrockStreamError,
    BedrockThrottlingError,
    BedrockTimeoutError,
    BedrockValidationError,
)
from src.ai.model_registry import DynamicValidator, StaticModelCatalog
from src.ai.response_parser import parse_converse_response, parse_converse_stream_events
from src.ai.types import (
    BedrockConverseResponse,
    BedrockStreamChunk,
    ModelCapabilities,
    TokenUsage,
    VerifiedModelCapabilities,
)

__all__ = [
    "BedrockClient",
    "ConversationManager",
    "DynamicValidator",
    "StaticModelCatalog",
    "BedrockConverseResponse",
    "BedrockStreamChunk",
    "ModelCapabilities",
    "TokenUsage",
    "VerifiedModelCapabilities",
    "BedrockError",
    "BedrockCredentialsError",
    "BedrockAccessDeniedError",
    "BedrockModelNotFoundError",
    "BedrockThrottlingError",
    "BedrockValidationError",
    "BedrockTimeoutError",
    "BedrockStreamError",
    "parse_converse_response",
    "parse_converse_stream_events",
]
