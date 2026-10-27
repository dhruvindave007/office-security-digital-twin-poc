# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""On-demand Bedrock token prices used to record the cost of every AI call.

Source: AWS public Price List files (AmazonBedrock / AmazonBedrockFoundationModels offers,
us-east-1), read 2026-10-13. Prices change: re-check before relying on them for budgeting.
"""
from typing import Dict, Optional, Tuple

# model_id -> (USD per 1M input tokens, USD per 1M output tokens)
PRICES_PER_MILLION: Dict[str, Tuple[float, float]] = {
    "us.amazon.nova-micro-v1:0": (0.035, 0.14),
    "us.amazon.nova-lite-v1:0": (0.06, 0.24),
    "us.amazon.nova-pro-v1:0": (0.80, 3.20),
    "global.amazon.nova-2-lite-v1:0": (0.30, 2.50),
    "global.anthropic.claude-haiku-5-5": (0.10, 0.50),
    "global.anthropic.claude-sonnet-5-5": (2.00, 10.00),
    "global.anthropic.claude-opus-5-5": (4.00, 20.00),
}


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """Cost of one call, or None when the model has no recorded price (never guess)."""
    price = PRICES_PER_MILLION.get(model_id)
    if price is None:
        return None
    return round(input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1], 8)
