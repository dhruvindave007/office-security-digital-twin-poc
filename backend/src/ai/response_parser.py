import time
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

from src.ai.errors import BedrockStreamError
from src.ai.types import BedrockConverseResponse, BedrockStreamChunk, TokenUsage


def parse_converse_response(
    raw_response: Dict[str, Any],
    model_id: str,
    region: str,
    client_latency_ms: float,
) -> BedrockConverseResponse:
    """Parse a synchronous Bedrock Converse API response into BedrockConverseResponse.

    Normalizes token metrics into canonical TokenUsage (Fix D) and extracts text
    content and operational metadata.
    """
    output = raw_response.get("output", {})
    message = output.get("message", {})
    content = message.get("content", [])

    text_parts: List[str] = [
        b["text"] for b in content if isinstance(b, dict) and "text" in b
    ]
    combined_text = "".join(text_parts)

    raw_usage = raw_response.get("usage", {})
    usage = TokenUsage(
        input_tokens=raw_usage.get("inputTokens", 0),
        output_tokens=raw_usage.get("outputTokens", 0),
        total_tokens=raw_usage.get("totalTokens", 0),
        cache_read_tokens=raw_usage.get("cacheReadInputTokens"),
        cache_write_tokens=raw_usage.get("cacheWriteInputTokens"),
    )

    request_id = raw_response.get("ResponseMetadata", {}).get("RequestId", "")
    stop_reason = raw_response.get("stopReason", "end_turn")

    return BedrockConverseResponse(
        text=combined_text,
        model_id=model_id,
        region=region,
        request_id=request_id,
        latency_ms=client_latency_ms,
        usage=usage,
        stop_reason=stop_reason,
        streamed=False,
        timestamp=datetime.utcnow().isoformat(),
        content_blocks=content,
    )


def parse_converse_stream_events(
    event_stream: Any,
    model_id: str,
    region: str,
    start_time: float,
) -> Generator[BedrockStreamChunk, None, BedrockConverseResponse]:
    """Parse Bedrock ConverseStream event stream into an incremental generator.

    Yields BedrockStreamChunk instances containing incremental text deltas,
    monitors for mid-stream error envelopes, and yields/returns a final chunk
    with the complete aggregated BedrockConverseResponse.
    """
    accumulated_text: List[str] = []
    stop_reason: str = "end_turn"
    raw_usage: Dict[str, Any] = {}

    stream_error_keys = [
        "throttlingException",
        "internalServerException",
        "modelStreamErrorException",
        "validationException",
        "serviceUnavailableException",
    ]

    for event in event_stream:
        # Check for mid-stream error envelopes emitted by Bedrock Runtime
        for err_key in stream_error_keys:
            if err_key in event:
                err_data = event[err_key]
                msg = err_data.get("message", err_key) if isinstance(err_data, dict) else str(err_data)
                raise BedrockStreamError(f"Bedrock stream error [{err_key}]: {msg}")

        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            text_chunk = delta.get("text", "")
            if text_chunk:
                accumulated_text.append(text_chunk)
                yield BedrockStreamChunk(
                    delta_text=text_chunk,
                    is_final=False,
                    stop_reason=None,
                    accumulated_text="".join(accumulated_text),
                )
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason", "end_turn")
        elif "metadata" in event:
            raw_usage = event["metadata"].get("usage", {})

    total_text = "".join(accumulated_text)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    usage = TokenUsage(
        input_tokens=raw_usage.get("inputTokens", 0),
        output_tokens=raw_usage.get("outputTokens", 0),
        total_tokens=raw_usage.get("totalTokens", 0),
        cache_read_tokens=raw_usage.get("cacheReadInputTokens"),
        cache_write_tokens=raw_usage.get("cacheWriteInputTokens"),
    )

    final_content_blocks: List[Dict[str, Any]] = (
        [{"text": total_text}] if total_text else []
    )

    final_response = BedrockConverseResponse(
        text=total_text,
        model_id=model_id,
        region=region,
        request_id="",
        latency_ms=elapsed_ms,
        usage=usage,
        stop_reason=stop_reason,
        streamed=True,
        timestamp=datetime.utcnow().isoformat(),
        content_blocks=final_content_blocks,
    )

    yield BedrockStreamChunk(
        delta_text="",
        is_final=True,
        stop_reason=stop_reason,
        accumulated_text=total_text,
        response=final_response,
    )

    return final_response
