# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import logging
import re
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError
from src.ai.errors import BedrockValidationError
from src.ai.types import VerifiedModelCapabilities

logger = logging.getLogger("office_vision.model_registry")

MODEL_ID_PATTERN = re.compile(
    r"^(?:[a-z]{2}\.[a-z0-9\-\.\_]+:[a-z0-9\-\.\_]+|[a-z0-9\-\.\_]+|arn:aws:bedrock:[a-z0-9\-]+:\d{12}:(?:application-)?inference-profile\/[a-zA-Z0-9\-\.\_\:]+)$"
)


class StaticModelCatalog:
    """Baseline catalog of preferred configured models and fallback capability definitions."""

    _CATALOG: Dict[str, VerifiedModelCapabilities] = {
        "us.amazon.nova-lite-v1:0": VerifiedModelCapabilities(
            model_id="us.amazon.nova-lite-v1:0",
            supports_converse=True,
            supports_streaming=True,
            supports_tools=True,
            supports_multimodal=True,
            supports_video=True,
            max_context_tokens=300_000,
            source="static_catalog",
        ),
        "us.amazon.nova-pro-v1:0": VerifiedModelCapabilities(
            model_id="us.amazon.nova-pro-v1:0",
            supports_converse=True,
            supports_streaming=True,
            supports_tools=True,
            supports_multimodal=True,
            supports_video=True,
            max_context_tokens=300_000,
            source="static_catalog",
        ),
        "us.amazon.nova-micro-v1:0": VerifiedModelCapabilities(
            model_id="us.amazon.nova-micro-v1:0",
            supports_converse=True,
            supports_streaming=True,
            supports_tools=False,
            supports_multimodal=False,
            supports_video=False,
            max_context_tokens=128_000,
            source="static_catalog",
        ),
        "us.anthropic.claude-3-5-sonnet-20241022-v2:0": VerifiedModelCapabilities(
            model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            supports_converse=True,
            supports_streaming=True,
            supports_tools=True,
            supports_multimodal=True,
            supports_video=False,
            max_context_tokens=200_000,
            source="static_catalog",
        ),
        # Chosen investigation model (2026-10-13 eval: best grounding/honesty per dollar).
        # Images yes, video no (send key frames). Context size: conservative assumption.
        "global.anthropic.claude-haiku-5-5": VerifiedModelCapabilities(
            model_id="global.anthropic.claude-haiku-5-5",
            supports_converse=True,
            supports_streaming=True,
            supports_tools=True,
            supports_multimodal=True,
            supports_video=False,
            max_context_tokens=200_000,
            source="static_catalog",
        ),
        "amazon.titan-text-express-v1": VerifiedModelCapabilities(
            model_id="amazon.titan-text-express-v1",
            supports_converse=True,
            supports_streaming=True,
            supports_tools=False,
            supports_multimodal=False,
            supports_video=False,
            max_context_tokens=8_000,
            source="static_catalog",
        ),
    }

    @classmethod
    def get_baseline(cls, model_id: str) -> VerifiedModelCapabilities:
        if model_id in cls._CATALOG:
            return cls._CATALOG[model_id]
        return VerifiedModelCapabilities(
            model_id=model_id,
            supports_converse=True,
            supports_streaming=True,
            source="static_catalog",
        )


class DynamicValidator:
    """Validates model capabilities dynamically against AWS metadata with cached fallback."""

    def __init__(self, region_name: str = "us-east-1"):
        self.region_name = region_name
        self._cache: Dict[str, VerifiedModelCapabilities] = {}

    def validate_id_format(self, model_id: str) -> str:
        """Validate that model_id conforms to AWS model identifier or inference profile regex."""
        stripped = model_id.strip()
        if not stripped or not MODEL_ID_PATTERN.match(stripped):
            raise BedrockValidationError(f"Invalid Bedrock model ID pattern: '{stripped}'")
        return stripped

    def validate_and_cache(
        self,
        model_id: str,
        boto_control_client: Optional[Any] = None,
    ) -> VerifiedModelCapabilities:
        """Validate model capabilities against AWS control plane or fallback to static catalog."""
        valid_id = self.validate_id_format(model_id)
        if valid_id in self._cache:
            return self._cache[valid_id]

        # Attempt dynamic AWS control plane inspection if client provided
        if boto_control_client:
            try:
                # Differentiate cross-region inference profiles vs foundation models
                if valid_id.startswith("us.") or valid_id.startswith("eu.") or "inference-profile" in valid_id:
                    profile = boto_control_client.get_inference_profile(
                        inferenceProfileIdentifier=valid_id
                    )
                    status = profile.get("status", "ACTIVE")
                    # Underlying models for system-defined profiles support converse and streaming
                    baseline = StaticModelCatalog.get_baseline(valid_id)
                    caps = VerifiedModelCapabilities(
                        model_id=valid_id,
                        supports_converse=True,
                        supports_streaming=True,
                        supports_tools=baseline.supports_tools,
                        supports_multimodal=baseline.supports_multimodal,
                        supports_video=baseline.supports_video,
                        max_context_tokens=baseline.max_context_tokens,
                        source="dynamic_aws",
                    )
                    self._cache[valid_id] = caps
                    return caps
                else:
                    response = boto_control_client.get_foundation_model(
                        modelIdentifier=valid_id
                    )
                    details = response.get("modelDetails", {})
                    output_modalities = details.get("outputModalities", [])
                    input_modalities = details.get("inputModalities", [])
                    streaming = details.get("responseStreamingSupported", True)

                    caps = VerifiedModelCapabilities(
                        model_id=valid_id,
                        supports_converse="TEXT" in output_modalities,
                        supports_streaming=bool(streaming),
                        supports_multimodal="IMAGE" in input_modalities,
                        supports_video="VIDEO" in input_modalities,
                        source="dynamic_aws",
                    )
                    self._cache[valid_id] = caps
                    return caps
            except (ClientError, Exception) as err:
                logger.info(
                    "Dynamic validation failed for '%s' (%s); falling back to static catalog",
                    valid_id,
                    type(err).__name__,
                )

        # Fallback to static catalog definition
        caps = StaticModelCatalog.get_baseline(valid_id)
        self._cache[valid_id] = caps
        return caps

    def get_cached(self, model_id: str) -> Optional[VerifiedModelCapabilities]:
        return self._cache.get(model_id)
