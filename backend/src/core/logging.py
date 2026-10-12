import json
import logging
from typing import Optional

# Dedicated structured logger for Bedrock runtime telemetry
bedrock_logger = logging.getLogger("office_vision.bedrock")


def log_bedrock_request(
    request_id: str,
    model_id: str,
    latency_ms: float,
    streamed: bool,
    retry_count: int,
    status: str,
    error_type: Optional[str] = None,
) -> None:
    """Log Bedrock operational metadata only.

    NEVER logs prompts, responses, headers, credentials, or secrets.
    """
    record = {
        "event": "bedrock_invocation",
        "request_id": request_id,
        "model_id": model_id,
        "latency_ms": round(latency_ms, 2),
        "streamed": streamed,
        "retry_count": retry_count,
        "status": status,
    }
    if error_type:
        record["error_type"] = error_type

    payload = json.dumps(record)
    if status == "success":
        bedrock_logger.info(payload)
    else:
        bedrock_logger.warning(payload)
