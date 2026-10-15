import time
from typing import Any, Dict, Generator, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
    SSOError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

# Expired/missing AWS SSO sign-in surfaces as these (not NoCredentialsError); treat them as a
# credentials problem so callers can tell the user to sign in again instead of "unexpected error".
_CREDENTIAL_ERRORS = (NoCredentialsError, PartialCredentialsError, SSOError, SSOTokenLoadError,
                      TokenRetrievalError, UnauthorizedSSOTokenError)
SIGN_IN_HINT = "AWS sign-in missing or expired. Run scripts/aws_sso_login.py and approve in the browser."
from src.ai.errors import (
    BedrockAccessDeniedError,
    BedrockCredentialsError,
    BedrockError,
    BedrockModelNotFoundError,
    BedrockThrottlingError,
    BedrockTimeoutError,
    BedrockValidationError,
)
from src.ai.model_registry import DynamicValidator
from src.ai.response_parser import parse_converse_response, parse_converse_stream_events
from src.ai.types import BedrockConverseResponse, BedrockStreamChunk
from src.core.logging import log_bedrock_request


def _inference_config(max_tokens: int, temperature: Optional[float]) -> Dict[str, Any]:
    """Converse inferenceConfig. temperature=None omits it (Claude 5.x models reject the parameter)."""
    config: Dict[str, Any] = {"maxTokens": max_tokens}
    if temperature is not None:
        config["temperature"] = temperature
    return config


class BedrockClient:
    """Amazon Bedrock Runtime client with structured logging and adaptive retries.

    Interacts strictly via the Amazon Bedrock Converse and ConverseStream APIs.
    Enforces sanitized operational logging and graceful error classification.
    """

    def __init__(
        self,
        region_name: str = "us-east-1",
        default_model_id: str = "us.amazon.nova-lite-v1:0",
        connect_timeout: float = 10.0,
        read_timeout: float = 60.0,
        max_retries: int = 3,
        boto_client: Optional[Any] = None,
        validator: Optional[DynamicValidator] = None,
    ):
        self.region_name = region_name
        self.validator = validator or DynamicValidator(region_name=region_name)
        self.default_model_id = self.validator.validate_id_format(default_model_id)
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_retries = max_retries
        self._boto_client = boto_client

    @property
    def client(self) -> Any:
        """Lazily initialize and return the boto3 bedrock-runtime client."""
        if self._boto_client is None:
            config = Config(
                region_name=self.region_name,
                retries={"max_attempts": self.max_retries, "mode": "adaptive"},
                connect_timeout=self.connect_timeout,
                read_timeout=self.read_timeout,
            )
            self._boto_client = boto3.client("bedrock-runtime", config=config)
        return self._boto_client

    def is_credentialed(self) -> Tuple[bool, str]:
        """Check if AWS credentials can be resolved via standard credential chain.

        Non-billable check that inspects provider resolution without invoking inference.
        """
        try:
            session = boto3.Session(region_name=self.region_name)
            creds = session.get_credentials()
            if creds is None:
                return False, "No AWS credentials resolved in standard provider chain."
            return True, f"Credentials resolved via {creds.method}"
        except Exception as e:
            return False, f"Credential resolution failed: {type(e).__name__} - {e}"

    def converse(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        model_id: Optional[str] = None,
        max_tokens: int = 512,
        temperature: Optional[float] = 0.7,
        additional_fields: Optional[Dict[str, Any]] = None,
    ) -> BedrockConverseResponse:
        """Execute a synchronous Converse call with telemetry logging and error mapping."""
        active_model = self.validator.validate_id_format(model_id or self.default_model_id)
        kwargs: Dict[str, Any] = {
            "modelId": active_model,
            "messages": messages,
            "inferenceConfig": _inference_config(max_tokens, temperature),
        }
        if system_prompt and system_prompt.strip():
            kwargs["system"] = [{"text": system_prompt.strip()}]
        if additional_fields:
            kwargs["additionalModelRequestFields"] = additional_fields

        start_time = time.perf_counter()
        try:
            raw_response = self.client.converse(**kwargs)
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            parsed = parse_converse_response(raw_response, active_model, self.region_name, latency_ms)
            log_bedrock_request(
                request_id=parsed.request_id,
                model_id=active_model,
                latency_ms=latency_ms,
                streamed=False,
                retry_count=0,
                status="success",
            )
            return parsed
        except _CREDENTIAL_ERRORS as e:
            self._log_error(active_model, start_time, "credentials_error")
            raise BedrockCredentialsError(SIGN_IN_HINT) from e
        except (ConnectTimeoutError, ReadTimeoutError) as e:
            self._log_error(active_model, start_time, "timeout_error")
            raise BedrockTimeoutError(f"Bedrock request timed out: {e}") from e
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "ClientError")
            self._log_error(active_model, start_time, err_code)
            self._map_client_error(e, active_model)
        except Exception as e:
            self._log_error(active_model, start_time, type(e).__name__)
            raise BedrockError(f"Unexpected error during Bedrock converse: {e}") from e

    def converse_stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        model_id: Optional[str] = None,
        max_tokens: int = 512,
        temperature: Optional[float] = 0.7,
        additional_fields: Optional[Dict[str, Any]] = None,
    ) -> Generator[BedrockStreamChunk, None, BedrockConverseResponse]:
        """Execute a streaming ConverseStream call, yielding BedrockStreamChunk instances."""
        active_model = self.validator.validate_id_format(model_id or self.default_model_id)
        kwargs: Dict[str, Any] = {
            "modelId": active_model,
            "messages": messages,
            "inferenceConfig": _inference_config(max_tokens, temperature),
        }
        if system_prompt and system_prompt.strip():
            kwargs["system"] = [{"text": system_prompt.strip()}]
        if additional_fields:
            kwargs["additionalModelRequestFields"] = additional_fields

        start_time = time.perf_counter()
        try:
            raw_response = self.client.converse_stream(**kwargs)
            stream = raw_response.get("stream")
            log_bedrock_request(
                request_id="",
                model_id=active_model,
                latency_ms=0.0,
                streamed=True,
                retry_count=0,
                status="stream_opened",
            )
            return parse_converse_stream_events(stream, active_model, self.region_name, start_time)
        except _CREDENTIAL_ERRORS as e:
            self._log_error(active_model, start_time, "credentials_error")
            raise BedrockCredentialsError(SIGN_IN_HINT) from e
        except (ConnectTimeoutError, ReadTimeoutError) as e:
            self._log_error(active_model, start_time, "timeout_error")
            raise BedrockTimeoutError(f"Bedrock stream connection timed out: {e}") from e
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "ClientError")
            self._log_error(active_model, start_time, err_code)
            self._map_client_error(e, active_model)
        except Exception as e:
            self._log_error(active_model, start_time, type(e).__name__)
            raise BedrockError(f"Unexpected error during Bedrock converse_stream: {e}") from e

    def _log_error(self, model_id: str, start_time: float, err_code: str) -> None:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        log_bedrock_request(
            request_id="",
            model_id=model_id,
            latency_ms=latency_ms,
            streamed=False,
            retry_count=0,
            status="error",
            error_type=err_code,
        )

    def _map_client_error(self, e: ClientError, model_id: str) -> None:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        msg = e.response.get("Error", {}).get("Message", str(e))
        if code == "AccessDeniedException":
            raise BedrockAccessDeniedError(
                f"Access denied for model '{model_id}'. Enable model access in AWS Bedrock Console. [{msg}]"
            ) from e
        elif code == "ResourceNotFoundException":
            raise BedrockModelNotFoundError(
                f"Model or profile '{model_id}' not found in region '{self.region_name}'. [{msg}]"
            ) from e
        elif code == "ThrottlingException":
            raise BedrockThrottlingError(f"Bedrock rate limit exceeded: {msg}") from e
        elif code == "ValidationException":
            raise BedrockValidationError(f"Converse API validation failed: {msg}") from e
        raise BedrockError(f"AWS Bedrock ClientError [{code}]: {msg}") from e
