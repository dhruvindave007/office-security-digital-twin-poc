import pytest
from src.ai.bedrock_client import BedrockClient
from src.ai.errors import (
    BedrockAccessDeniedError,
    BedrockCredentialsError,
    BedrockModelNotFoundError,
)
from src.config import get_settings


def test_bedrock_live_converse():
    """Live smoke test invoking Amazon Bedrock Converse API with exact token handshake."""
    settings = get_settings()
    client = BedrockClient(
        region_name=settings.aws_region,
        default_model_id=settings.bedrock_reasoning_model_id,
    )

    is_cred, cred_info = client.is_credentialed()
    if not is_cred:
        pytest.skip(f"Live Bedrock skipped: {cred_info}")

    try:
        response = client.converse(
            messages=[
                {"role": "user", "content": [{"text": "Reply with exactly: BEDROCK_M1_OK"}]}
            ],
            system_prompt="You are a test harness assistant. Adhere strictly to the requested exact output string.",
            max_tokens=64,
            temperature=0.0,
        )

        print(
            f"\n[LIVE BEDROCK SUCCESS] Model: {response.model_id}, "
            f"Latency: {response.latency_ms:.1f}ms, Output: {response.text.strip()}"
        )

        assert "BEDROCK_M1_OK" in response.text.strip()
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0
        assert response.streamed is False
    except BedrockCredentialsError as e:
        pytest.skip(f"AWS credentials could not authenticate: {e}")
    except BedrockAccessDeniedError as e:
        pytest.skip(f"Bedrock access denied (IAM or model access not granted): {e}")
    except BedrockModelNotFoundError as e:
        pytest.skip(f"Configured model not available in region '{settings.aws_region}': {e}")
