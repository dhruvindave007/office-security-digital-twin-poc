# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import boto3
from botocore.exceptions import ReadTimeoutError
from botocore.stub import Stubber
import pytest
from src.ai.bedrock_client import BedrockClient
from src.ai.errors import (
    BedrockAccessDeniedError,
    BedrockModelNotFoundError,
    BedrockThrottlingError,
    BedrockTimeoutError,
    BedrockValidationError,
)


@pytest.fixture
def mock_bedrock_client():
    """Create a BedrockClient backed by a botocore Stubber."""
    boto_client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="mock_access_key",
        aws_secret_access_key="mock_secret_key",
    )
    client = BedrockClient(
        region_name="us-east-1",
        default_model_id="us.amazon.nova-lite-v1:0",
        boto_client=boto_client,
    )
    stubber = Stubber(boto_client)
    return client, stubber


def test_converse_success(mock_bedrock_client):
    """Verify BedrockClient.converse succeeds and parses response using botocore Stubber."""
    client, stubber = mock_bedrock_client

    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "Hello from mock Bedrock!"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": 10,
            "outputTokens": 6,
            "totalTokens": 16,
        },
        "metrics": {
            "latencyMs": 150,
        },
    }

    stubber.add_response("converse", response)
    with stubber:
        res = client.converse(
            messages=[{"role": "user", "content": [{"text": "Hi"}]}],
            system_prompt="You are a test assistant.",
        )

    assert res.text == "Hello from mock Bedrock!"
    assert res.model_id == "us.amazon.nova-lite-v1:0"
    assert res.usage.total_tokens == 16
    assert res.stop_reason == "end_turn"
    assert res.streamed is False


def test_converse_access_denied_error(mock_bedrock_client):
    """Verify AccessDeniedException is mapped to BedrockAccessDeniedError."""
    client, stubber = mock_bedrock_client

    stubber.add_client_error(
        "converse",
        service_error_code="AccessDeniedException",
        service_message="Model access has not been requested or granted in this account.",
    )

    with stubber:
        with pytest.raises(BedrockAccessDeniedError) as exc_info:
            client.converse(messages=[{"role": "user", "content": [{"text": "Hi"}]}])

    assert "Access denied" in str(exc_info.value)
    assert "us.amazon.nova-lite-v1:0" in str(exc_info.value)


def test_converse_model_not_found_error(mock_bedrock_client):
    """Verify ResourceNotFoundException is mapped to BedrockModelNotFoundError."""
    client, stubber = mock_bedrock_client

    stubber.add_client_error(
        "converse",
        service_error_code="ResourceNotFoundException",
        service_message="The specified model was not found.",
    )

    with stubber:
        with pytest.raises(BedrockModelNotFoundError) as exc_info:
            client.converse(messages=[{"role": "user", "content": [{"text": "Hi"}]}])

    assert "not found" in str(exc_info.value)


def test_converse_throttling_error(mock_bedrock_client):
    """Verify ThrottlingException is mapped to BedrockThrottlingError."""
    client, stubber = mock_bedrock_client

    stubber.add_client_error(
        "converse",
        service_error_code="ThrottlingException",
        service_message="Rate limit exceeded.",
    )

    with stubber:
        with pytest.raises(BedrockThrottlingError) as exc_info:
            client.converse(messages=[{"role": "user", "content": [{"text": "Hi"}]}])

    assert "rate limit exceeded" in str(exc_info.value)


def test_converse_validation_error(mock_bedrock_client):
    """Verify ValidationException is mapped to BedrockValidationError."""
    client, stubber = mock_bedrock_client

    stubber.add_client_error(
        "converse",
        service_error_code="ValidationException",
        service_message="Input token length exceeds allowed max limit.",
    )

    with stubber:
        with pytest.raises(BedrockValidationError) as exc_info:
            client.converse(messages=[{"role": "user", "content": [{"text": "Hi"}]}])

    assert "validation failed" in str(exc_info.value)


def test_converse_timeout_error(mock_bedrock_client, monkeypatch):
    """Verify timeout exception is mapped to BedrockTimeoutError."""
    client, _ = mock_bedrock_client

    def mock_converse_timeout(**kwargs):
        raise ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com")

    monkeypatch.setattr(client.client, "converse", mock_converse_timeout)

    with pytest.raises(BedrockTimeoutError):
        client.converse(messages=[{"role": "user", "content": [{"text": "Hi"}]}])


def test_is_credentialed_method():
    """Verify is_credentialed returns boolean tuple."""
    client = BedrockClient()
    is_cred, info = client.is_credentialed()
    assert isinstance(is_cred, bool)
    assert isinstance(info, str)
