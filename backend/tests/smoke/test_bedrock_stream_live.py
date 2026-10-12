import pytest
from src.ai.bedrock_client import BedrockClient
from src.ai.errors import (
    BedrockAccessDeniedError,
    BedrockCredentialsError,
    BedrockModelNotFoundError,
)
from src.config import get_settings


def test_bedrock_live_converse_stream():
    """Live smoke test invoking Amazon Bedrock ConverseStream API with incremental chunks."""
    settings = get_settings()
    client = BedrockClient(
        region_name=settings.aws_region,
        default_model_id=settings.bedrock_reasoning_model_id,
    )

    is_cred, cred_info = client.is_credentialed()
    if not is_cred:
        pytest.skip(f"Live Bedrock streaming skipped: {cred_info}")

    try:
        generator = client.converse_stream(
            messages=[
                {"role": "user", "content": [{"text": "Reply with exactly: BEDROCK_STREAM_OK"}]}
            ],
            system_prompt="You are a test harness assistant. Adhere strictly to the requested exact output string.",
            max_tokens=64,
            temperature=0.0,
        )

        chunks = list(generator)
        assert len(chunks) > 0

        final_chunk = chunks[-1]
        assert final_chunk.is_final is True
        assert final_chunk.response is not None

        accumulated = final_chunk.accumulated_text.strip()
        print(
            f"\n[LIVE BEDROCK STREAM SUCCESS] Model: {final_chunk.response.model_id}, "
            f"Latency: {final_chunk.response.latency_ms:.1f}ms, Chunks: {len(chunks)}, Output: {accumulated}"
        )

        assert "BEDROCK_STREAM_OK" in accumulated
        assert final_chunk.response.usage.output_tokens > 0
        assert final_chunk.response.streamed is True
    except BedrockCredentialsError as e:
        pytest.skip(f"AWS credentials could not authenticate: {e}")
    except BedrockAccessDeniedError as e:
        pytest.skip(f"Bedrock access denied (IAM or model access not granted): {e}")
    except BedrockModelNotFoundError as e:
        pytest.skip(f"Configured model not available in region '{settings.aws_region}': {e}")
