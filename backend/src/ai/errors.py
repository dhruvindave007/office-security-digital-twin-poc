# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
class BedrockError(Exception):
    """Base exception for all Amazon Bedrock client errors."""
    pass


class BedrockCredentialsError(BedrockError):
    """Raised when AWS credentials cannot be resolved or are incomplete."""
    pass


class BedrockAccessDeniedError(BedrockError):
    """Raised when IAM permissions or Bedrock console model access is denied."""
    pass


class BedrockModelNotFoundError(BedrockError):
    """Raised when the specified model ID or inference profile does not exist."""
    pass


class BedrockThrottlingError(BedrockError):
    """Raised when Bedrock API rate limits or service quotas are exceeded."""
    pass


class BedrockValidationError(BedrockError):
    """Raised when Converse API parameters or payload structure are rejected."""
    pass


class BedrockTimeoutError(BedrockError):
    """Raised when connection or socket read times out."""
    pass


class BedrockStreamError(BedrockError):
    """Raised when an error envelope is received during event stream iteration."""
    pass
