"""Build, write, read and gate on `.preflight/verdict.json`.

The gate never trusts the file's own `verdict` field: it recomputes verdict, blocking list and
summary from `results` and refuses a file that disagrees with itself.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import gitutil
from .checks.base import shorten
from .model import NOT_READY, PASS, READY, STATUSES, Result, Ruleset
from .timeutil import parse_timestamp

SCHEMA = 1
VERDICT_DIR = gitutil.IGNORED_DIR
VERDICT_FILE = "verdict.json"
DEFAULT_MAX_AGE_MIN = 120.0
CLOCK_SKEW_MIN = 5.0  # a verdict dated further in the future than this is refused


def verdict_path(project: Path | str) -> Path:
    return Path(project) / VERDICT_DIR / VERDICT_FILE


def summarize(results: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
    for result in results:
        counts[result["status"].lower()] += 1
    return counts


def decide(results: Sequence[dict[str, Any]]) -> tuple[str, list[str]]:
    """THE rule: READY only if every required check passed. Returns (verdict, blocking ids)."""
    blocking = [r["id"] for r in results if r["required"] and r["status"] != PASS]
    return (NOT_READY if blocking else READY), blocking


def build_document(
    ruleset: Ruleset,
    project: Path,
    commit: str | None,
    dirty: bool,
    results: Sequence[Result],
    created_at: str,
) -> dict[str, Any]:
    rows = [r.to_dict() for r in results]
    verdict, blocking = decide(rows)
    return {
        "schema": SCHEMA,
        "ruleset": {"name": ruleset.name, "version": ruleset.version, "source": ruleset.source},
        "project": str(project),
        "commit": commit,
        "dirty": dirty,
        "created_at": created_at,
        "results": rows,
        "summary": summarize(rows),
        "verdict": verdict,
        "blocking": blocking,
    }


def write_json(document: dict[str, Any], path: Path) -> None:
    """Write the document atomically (temp file, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


_RESULT_FIELDS: dict[str, type] = {
    "id": str, "title": str, "type": str, "required": bool, "status": str, "detail": str, "hint": str,
}


def validate_document(doc: Any) -> str | None:
    """Why this verdict.json cannot be trusted, or None when it is well-formed and self-consistent."""
    if not isinstance(doc, dict):
        return "verdict.json is not a JSON object"
    if doc.get("schema") != SCHEMA or isinstance(doc.get("schema"), bool):
        return f"verdict.json has schema {doc.get('schema')!r}, expected {SCHEMA}"
    top: dict[str, Any] = {
        "ruleset": dict, "project": str, "commit": (str, type(None)), "dirty": bool,
        "created_at": str, "results": list, "summary": dict, "verdict": str, "blocking": list,
    }
    for key, kind in top.items():
        if key not in doc or not isinstance(doc[key], kind):
            return f"verdict.json has a missing or malformed '{key}' field"
    ruleset = doc["ruleset"]
    if not (isinstance(ruleset.get("name"), str) and isinstance(ruleset.get("version"), int)
            and isinstance(ruleset.get("source"), str)):
        return "verdict.json has a malformed 'ruleset' field"
    try:
        parse_timestamp(doc["created_at"])
    except ValueError:
        return "verdict.json has a malformed 'created_at' (needs an ISO 8601 time with offset)"
    results = doc["results"]
    if not results:
        return "verdict.json has no results: nothing was verified"
    seen: set[str] = set()
    for row in results:
        if not isinstance(row, dict) or any(not isinstance(row.get(name), kind) for name, kind in _RESULT_FIELDS.items()):
            return "verdict.json has a malformed entry in 'results'"
        if row["status"] not in STATUSES or (row["required"] and row["status"] == "WARN"):
            return f"verdict.json has an impossible status for check '{row['id']}'"
        if row["id"] in seen:
            return f"verdict.json lists check '{row['id']}' twice"
        seen.add(row["id"])
    if not any(row["required"] for row in results):
        return "verdict.json has no required checks: nothing was verified"
    verdict, blocking = decide(results)
    if doc["verdict"] not in (READY, NOT_READY):
        return f"verdict.json has an unknown verdict {doc['verdict']!r}"
    if doc["verdict"] != verdict or doc["blocking"] != blocking or doc["summary"] != summarize(results):
        return "verdict.json does not match its own results (edited by hand?)"
    return None


@dataclass
class Gate:
    ok: bool
    message: str  # "GATE OK: ready at 1a21df4" or "GATE CLOSED: <reason>"
    document: dict[str, Any] | None = None


def _closed(reason: str) -> Gate:
    return Gate(False, f"GATE CLOSED: {reason}")


def check_gate(
    project: Path | str,
    max_age_min: float = DEFAULT_MAX_AGE_MIN,
    now: datetime | None = None,
) -> Gate:
    """Open only for a fresh, self-consistent READY verdict on the current commit and a clean tree."""
    project = Path(project)
    if not project.is_dir():
        return _closed(f"project folder not found: {project}")
    path = verdict_path(project)
    if not path.is_file():
        return _closed(f"no verdict file at {path} (run `python3 -m preflight run` first)")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _closed(f"{path.name} is not valid JSON")
    problem = validate_document(doc)
    if problem:
        return _closed(problem)
    if doc["verdict"] != READY:
        return _closed(f"verdict is NOT READY (blocking: {', '.join(doc['blocking'])})")
    commit = doc["commit"]
    if not commit:
        return _closed("verdict has no commit (the project had no commits when it ran)")
    if doc["dirty"]:
        return _closed("verdict was produced on a working tree with uncommitted changes")
    if not gitutil.is_repo(project):
        return _closed("project is not a git repository")
    head = gitutil.head_sha(project)
    if head != commit:
        return _closed(
            f"verdict is for commit {gitutil.short_sha(commit)} but HEAD is {gitutil.short_sha(head)}; run preflight again"
        )
    try:
        changes = gitutil.dirty_entries(project)
    except gitutil.GitError as exc:
        return _closed(f"cannot read the working tree state: {exc}")
    if changes:
        return _closed(f"working tree has {len(changes)} uncommitted change(s): {shorten(changes, 3)}")
    current = now or datetime.now(timezone.utc)
    age_min = (current - parse_timestamp(doc["created_at"])).total_seconds() / 60
    if age_min < -CLOCK_SKEW_MIN:
        return _closed("verdict is dated in the future (clock problem or edited file)")
    if age_min > max_age_min:
        return _closed(f"verdict is {age_min:.0f} min old (max {max_age_min:g}); run preflight again")
    return Gate(True, f"GATE OK: ready at {gitutil.short_sha(commit)}", doc)
