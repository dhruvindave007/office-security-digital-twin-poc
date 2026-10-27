# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from src.ai.response_parser import parse_converse_response
from src.ai.types import TokenUsage


def test_parse_converse_response_full():
    """Verify parsing of full Converse API dictionary into BedrockConverseResponse."""
    raw_response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Hello, "},
                    {"text": "world!"},
                ],
            }
        },
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": 25,
            "outputTokens": 10,
            "totalTokens": 35,
            "cacheReadInputTokens": 5,
            "cacheWriteInputTokens": 2,
        },
        "ResponseMetadata": {
            "RequestId": "test-request-id-12345",
            "HTTPStatusCode": 200,
        },
    }

    parsed = parse_converse_response(
        raw_response=raw_response,
        model_id="us.amazon.nova-lite-v1:0",
        region="us-east-1",
        client_latency_ms=145.5,
    )

    assert parsed.text == "Hello, world!"
    assert parsed.model_id == "us.amazon.nova-lite-v1:0"
    assert parsed.region == "us-east-1"
    assert parsed.request_id == "test-request-id-12345"
    assert parsed.latency_ms == 145.5
    assert parsed.stop_reason == "end_turn"
    assert parsed.streamed is False
    assert len(parsed.content_blocks) == 2

    # Canonical TokenUsage checks (Fix D)
    assert isinstance(parsed.usage, TokenUsage)
    assert parsed.usage.input_tokens == 25
    assert parsed.usage.output_tokens == 10
    assert parsed.usage.total_tokens == 35
    assert parsed.usage.cache_read_tokens == 5
    assert parsed.usage.cache_write_tokens == 2


def test_parse_converse_response_minimal():
    """Verify parsing handles omitted optional usage fields gracefully."""
    raw_response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "OK"}],
            }
        },
        "stopReason": "stop_sequence",
        "usage": {
            "inputTokens": 5,
            "outputTokens": 1,
            "totalTokens": 6,
        },
    }

    parsed = parse_converse_response(
        raw_response=raw_response,
        model_id="amazon.titan-text-express-v1",
        region="us-east-1",
        client_latency_ms=50.0,
    )

    assert parsed.text == "OK"
    assert parsed.stop_reason == "stop_sequence"
    assert parsed.usage.cache_read_tokens is None
    assert parsed.usage.cache_write_tokens is None
    assert parsed.request_id == ""
