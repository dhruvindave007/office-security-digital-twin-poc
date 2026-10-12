import time
import pytest
from src.ai.errors import BedrockStreamError
from src.ai.response_parser import parse_converse_stream_events


def test_parse_converse_stream_events_success():
    """Verify event streaming yields incremental chunks and completes with final BedrockConverseResponse."""
    events = [
        {"contentBlockDelta": {"delta": {"text": "Office "}}},
        {"contentBlockDelta": {"delta": {"text": "Vision "}}},
        {"contentBlockDelta": {"delta": {"text": "AI"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {
                    "inputTokens": 12,
                    "outputTokens": 3,
                    "totalTokens": 15,
                    "cacheReadInputTokens": 0,
                    "cacheWriteInputTokens": 0,
                }
            }
        },
    ]

    start_time = time.perf_counter()
    chunks = list(
        parse_converse_stream_events(
            event_stream=events,
            model_id="us.amazon.nova-lite-v1:0",
            region="us-east-1",
            start_time=start_time,
        )
    )

    # 3 content chunks + 1 final chunk
    assert len(chunks) == 4

    assert chunks[0].delta_text == "Office "
    assert chunks[0].is_final is False
    assert chunks[0].accumulated_text == "Office "

    assert chunks[1].delta_text == "Vision "
    assert chunks[1].accumulated_text == "Office Vision "

    assert chunks[2].delta_text == "AI"
    assert chunks[2].accumulated_text == "Office Vision AI"

    # Final summary chunk
    final_chunk = chunks[3]
    assert final_chunk.is_final is True
    assert final_chunk.stop_reason == "end_turn"
    assert final_chunk.accumulated_text == "Office Vision AI"
    assert final_chunk.response is not None
    assert final_chunk.response.streamed is True
    assert final_chunk.response.text == "Office Vision AI"
    assert final_chunk.response.usage.total_tokens == 15


def test_parse_converse_stream_events_mid_stream_error():
    """Verify stream parser raises BedrockStreamError on error envelopes in the event stream."""
    events = [
        {"contentBlockDelta": {"delta": {"text": "Chunk 1"}}},
        {
            "throttlingException": {
                "message": "Too many requests. Rate quota reached."
            }
        },
    ]

    start_time = time.perf_counter()
    generator = parse_converse_stream_events(
        event_stream=events,
        model_id="us.amazon.nova-lite-v1:0",
        region="us-east-1",
        start_time=start_time,
    )

    # First chunk yields normally
    chunk1 = next(generator)
    assert chunk1.delta_text == "Chunk 1"

    # Next event triggers BedrockStreamError
    with pytest.raises(BedrockStreamError) as exc_info:
        next(generator)

    assert "throttlingException" in str(exc_info.value)
    assert "Too many requests" in str(exc_info.value)
