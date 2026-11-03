"""Plain data types shared across the engine. No logic beyond tiny helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
STATUSES = (PASS, FAIL, WARN, SKIP)
READY, NOT_READY = "READY", "NOT_READY"


@dataclass(frozen=True)
class Outcome:
    """What a check type returns: PASS, FAIL or SKIP, plus the reason in one line."""

    status: str
    detail: str


def passed(detail: str) -> Outcome:
    return Outcome(PASS, detail)


def failed(detail: str) -> Outcome:
    return Outcome(FAIL, detail)


def skipped(detail: str) -> Outcome:
    """The check could not be verified (offline, tool missing, unreadable, ...)."""
    return Outcome(SKIP, detail)


@dataclass
class CheckSpec:
    """One `[[check]]` entry from a ruleset."""

    id: str
    title: str
    type: str
    required: bool
    fix_hint: str
    params: dict[str, Any]


@dataclass
class Ruleset:
    """A parsed and validated ruleset file."""

    name: str
    version: int
    source: str
    rubric: list[tuple[str, float]]
    checks: list[CheckSpec]
    path: str


@dataclass
class Result:
    """The recorded outcome of one check in one run (one row of the report)."""

    id: str
    title: str
    type: str
    required: bool
    status: str
    detail: str
    hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "required": self.required,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        }
