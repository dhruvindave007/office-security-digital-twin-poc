# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from unittest.mock import MagicMock

import pytest
from src.ai.errors import BedrockValidationError
from src.ai.model_registry import DynamicValidator, StaticModelCatalog


def test_static_model_catalog():
    """Verify static catalog returns known baselines and graceful fallbacks."""
    nova_lite = StaticModelCatalog.get_baseline("us.amazon.nova-lite-v1:0")
    assert nova_lite.supports_converse is True
    assert nova_lite.supports_streaming is True
    assert nova_lite.supports_tools is True
    assert nova_lite.supports_multimodal is True
    assert nova_lite.supports_video is True
    assert nova_lite.source == "static_catalog"

    titan = StaticModelCatalog.get_baseline("amazon.titan-text-express-v1")
    assert titan.supports_tools is False
    assert titan.supports_multimodal is False

    unknown = StaticModelCatalog.get_baseline("custom.fine-tuned-model:1")
    assert unknown.supports_converse is True
    assert unknown.supports_streaming is True
    assert unknown.source == "static_catalog"


def test_validator_id_format():
    """Verify validation of model ID patterns."""
    validator = DynamicValidator()

    # Valid patterns
    assert validator.validate_id_format("us.amazon.nova-lite-v1:0") == "us.amazon.nova-lite-v1:0"
    assert validator.validate_id_format("amazon.titan-text-express-v1") == "amazon.titan-text-express-v1"
    arn = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    assert validator.validate_id_format(arn) == arn

    # Invalid patterns
    with pytest.raises(BedrockValidationError):
        validator.validate_id_format("")
    with pytest.raises(BedrockValidationError):
        validator.validate_id_format("   ")
    with pytest.raises(BedrockValidationError):
        validator.validate_id_format("invalid model id with spaces")
    with pytest.raises(BedrockValidationError):
        validator.validate_id_format("invalid/char$model")


def test_dynamic_validator_with_mock_client():
    """Verify dynamic inspection with mock AWS control plane."""
    validator = DynamicValidator()
    mock_boto = MagicMock()
    mock_boto.get_inference_profile.return_value = {
        "inferenceProfileArn": "arn:aws:bedrock:...",
        "status": "ACTIVE",
    }

    caps = validator.validate_and_cache("us.amazon.nova-lite-v1:0", boto_control_client=mock_boto)
    assert caps.source == "dynamic_aws"
    assert caps.supports_converse is True
    assert caps.supports_streaming is True
    mock_boto.get_inference_profile.assert_called_once_with(
        inferenceProfileIdentifier="us.amazon.nova-lite-v1:0"
    )

    # Check caching: subsequent call should not invoke boto client
    mock_boto.reset_mock()
    cached = validator.validate_and_cache("us.amazon.nova-lite-v1:0", boto_control_client=mock_boto)
    assert cached == caps
    mock_boto.get_inference_profile.assert_not_called()


def test_dynamic_validator_fallback_on_client_error():
    """Verify graceful fallback to static catalog on AWS control plane error."""
    validator = DynamicValidator()
    mock_boto = MagicMock()
    mock_boto.get_inference_profile.side_effect = Exception("AWS AccessDeniedException")

    caps = validator.validate_and_cache("us.amazon.nova-lite-v1:0", boto_control_client=mock_boto)
    assert caps.source == "static_catalog"
    assert caps.model_id == "us.amazon.nova-lite-v1:0"
