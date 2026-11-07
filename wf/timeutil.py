"""Time stamps: ISO 8601 with the local offset, to the second. `WF_NOW` fixes the clock (tests, demos)."""

from __future__ import annotations

import os
from datetime import datetime


def now() -> str:
    fixed = os.environ.get("WF_NOW", "").strip()
    if fixed:
        return parse(fixed).isoformat(timespec="seconds")
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse(iso: str) -> datetime:
    when = datetime.fromisoformat(iso)
    return when if when.tzinfo else when.astimezone()


def hhmm(iso: str | None) -> str:
    return parse(iso).strftime("%H:%M") if iso else "?"


def minutes(start: str, end: str) -> int:
    """Whole minutes from START to END (never negative)."""
    return max(0, int((parse(end) - parse(start)).total_seconds() // 60))
