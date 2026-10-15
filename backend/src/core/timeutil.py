"""Canonical UTC timestamps for the event ledger.

Every timestamp written to the twin uses ONE fixed-width format: YYYY-MM-DDTHH:MM:SS.ffffffZ.
Because the width and zone are fixed, string order equals chronological order, so SQL text
comparisons (timestamp >= ?) are correct. Mixed formats ("Z" vs "+00:00", naive vs aware,
with/without fractional seconds) broke those comparisons and crashed naive/aware arithmetic.
"""
from datetime import datetime, timezone
from typing import Union

CANONICAL_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def parse_utc(value: Union[str, datetime]) -> datetime:
    """Parse an ISO-8601 string or datetime into an aware UTC datetime. Naive input is treated as UTC.

    Raises ValueError on anything that is not a valid ISO-8601 timestamp.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("timestamp must be a non-empty ISO-8601 string.")
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"Invalid ISO-8601 timestamp '{value}': {e}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_utc(value: Union[str, datetime]) -> str:
    """Return the canonical fixed-width UTC string for a timestamp (see module docstring)."""
    return parse_utc(value).strftime(CANONICAL_FORMAT)


def utc_now_canonical() -> str:
    return datetime.now(timezone.utc).strftime(CANONICAL_FORMAT)
