import json
import logging
from src.core.logging import log_bedrock_request


def test_log_bedrock_request_structure(caplog):
    """Verify that log_bedrock_request records required telemetry fields without leakage."""
    with caplog.at_level(logging.INFO, logger="office_vision.bedrock"):
        log_bedrock_request(
            request_id="req-12345",
            model_id="us.amazon.nova-lite-v1:0",
            latency_ms=123.456,
            streamed=False,
            retry_count=0,
            status="success",
        )

    assert len(caplog.records) == 1
    record_text = caplog.records[0].message
    data = json.loads(record_text)

    assert data["event"] == "bedrock_invocation"
    assert data["request_id"] == "req-12345"
    assert data["model_id"] == "us.amazon.nova-lite-v1:0"
    assert data["latency_ms"] == 123.46
    assert data["streamed"] is False
    assert data["retry_count"] == 0
    assert data["status"] == "success"

    # Assert no sensitive/prompt keys exist in logged JSON
    forbidden_keys = ["prompt", "response", "messages", "content", "authorization", "secret", "token"]
    for key in forbidden_keys:
        assert key not in data


def test_log_bedrock_request_error_record(caplog):
    """Verify error logging emits warning level with error_type."""
    with caplog.at_level(logging.WARNING, logger="office_vision.bedrock"):
        log_bedrock_request(
            request_id="req-failed-99",
            model_id="us.amazon.nova-lite-v1:0",
            latency_ms=45.67,
            streamed=True,
            retry_count=1,
            status="error",
            error_type="ThrottlingException",
        )

    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"
    data = json.loads(caplog.records[0].message)
    assert data["status"] == "error"
    assert data["error_type"] == "ThrottlingException"
