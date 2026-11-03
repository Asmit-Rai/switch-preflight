"""Timestamp helpers. Every timestamp the engine reads or writes carries a timezone."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_timestamp(value: Any) -> datetime:
    """Parse an ISO 8601 timestamp with a timezone (`Z` or `+05:30`). Raise ValueError."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip())
    else:
        raise ValueError("expected an ISO 8601 string such as 2026-11-03T18:31:00Z")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp needs a timezone, for example Z or +05:30")
    return parsed


def iso_utc(moment: datetime) -> str:
    """2026-11-03T18:31:00Z"""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    """Local time with offset, to the second: 2026-11-04T09:12:00+05:30"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()
