"""Load, merge and validate rulesets (TOML).

`parse_ruleset` returns every problem it can find, so `lint` can print them all at once;
`load_ruleset` is the strict version used by `run`.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checks import CHECK_TYPES
from .checks.base import Param
from .model import CheckSpec, Ruleset
from .timeutil import parse_timestamp

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_ID_LEN = 32
META_KEYS = ("name", "version", "source", "rubric", "extends")
CHECK_KEYS = ("id", "title", "type", "required", "fix_hint")
MAX_EXTENDS_DEPTH = 5


@dataclass(frozen=True)
class Problem:
    level: str  # ERROR or WARNING
    where: str  # "meta", "check 'license'", ...
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


class RulesetError(Exception):
    """The ruleset has errors. `problems` holds every ERROR found."""

    def __init__(self, problems: list[Problem]) -> None:
        super().__init__("; ".join(str(p) for p in problems))
        self.problems = problems


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bad_relpath(value: str) -> str | None:
    norm = value.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return "must be relative to the project"
    if ".." in norm.split("/"):
        return "must stay inside the project (no '..')"
    return None


def _regex_error(value: str) -> str | None:
    try:
        re.compile(value)
    except re.error as exc:
        return f"bad regex {value!r}: {exc}"
    return None


def _check_value(kind: str, value: Any, param: Param) -> str | None:
    """Return an error message, or None when `value` fits the parameter kind."""
    if kind in ("str", "relpath", "regex"):
        if not isinstance(value, str) or not value.strip():
            return "must be a non-empty string"
        if kind == "relpath":
            return _bad_relpath(value)
        if kind == "regex":
            return _regex_error(value)
        return None
    if kind in ("globs", "headings", "relpaths", "regexes"):
        if not isinstance(value, list) or not value:
            return "must be a non-empty list of strings"
        for item in value:
            if not isinstance(item, str) or not item.strip():
                return "must be a list of non-empty strings"
            if kind in ("globs", "relpaths") and (problem := _bad_relpath(item)):
                return f"{item!r} {problem}"
            if kind == "regexes" and (problem := _regex_error(item)):
                return problem
            if kind == "headings" and any(not option.strip() for option in item.split("|")):
                return f"{item!r} has an empty alternative around '|'"
        return None
    if kind == "flags":
        if not isinstance(value, str) or set(value) - set("ims"):
            return "must be a string of flag letters from i, m, s"
        return None
    if kind == "bool":
        return None if isinstance(value, bool) else "must be true or false"
    if kind in ("int", "number"):
        ok = isinstance(value, int) and not isinstance(value, bool) if kind == "int" else _is_number(value)
        if not ok:
            return "must be an integer" if kind == "int" else "must be a number"
        if param.minimum is not None and value < param.minimum:
            return f"must be at least {param.minimum:g}"
        return None
    if kind == "timestamp":
        try:
            parse_timestamp(value)
        except (ValueError, TypeError) as exc:
            return f"bad timestamp {value!r}: {exc}"
        return None
    return f"unsupported parameter kind {kind!r}"


def _read_raw(path: Path, depth: int, problems: list[Problem]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Parse one TOML file and, if it `extends` another, merge the base underneath."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        problems.append(Problem("ERROR", "file", f"cannot read {path}: {exc.strerror or exc}"))
        return None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        problems.append(Problem("ERROR", "file", f"cannot parse {path.name}: {exc}"))
        return None

    for key in data:
        if key not in ("meta", "check"):
            problems.append(Problem("WARNING", "file", f"unknown top-level key '{key}' ignored"))
    meta = data.get("meta", {})
    checks = data.get("check", [])
    if not isinstance(meta, dict):
        problems.append(Problem("ERROR", "meta", "[meta] must be a table"))
        meta = {}
    if not isinstance(checks, list):
        problems.append(Problem("ERROR", "file", "[[check]] entries must be an array of tables"))
        checks = []

    base_path = meta.get("extends")
    if base_path is None:
        return dict(meta), [c for c in checks if isinstance(c, dict)]
    if not isinstance(base_path, str) or not base_path.strip():
        problems.append(Problem("ERROR", "meta", "'extends' must be a path to another ruleset"))
        return dict(meta), [c for c in checks if isinstance(c, dict)]
    if depth >= MAX_EXTENDS_DEPTH:
        problems.append(Problem("ERROR", "meta", "'extends' chain is too deep (loop?)"))
        return None
    base = _read_raw((path.parent / base_path).resolve(), depth + 1, problems)
    if base is None:
        return None
    base_meta, base_checks = base
    merged_meta = {**base_meta, **{k: v for k, v in meta.items() if k != "extends"}}
    merged = [dict(c) for c in base_checks]
    for child in checks:
        if not isinstance(child, dict):
            continue
        for i, existing in enumerate(merged):
            if existing.get("id") == child.get("id"):
                merged[i] = {**existing, **child}  # same id: the child overrides the base entry
                break
        else:
            merged.append(dict(child))
    return merged_meta, merged


def _validate_meta(meta: dict[str, Any], problems: list[Problem]) -> tuple[str, int, str, list[tuple[str, float]]]:
    name, version, source, rubric = "", 0, "", []
    for key in meta:
        if key not in META_KEYS:
            problems.append(Problem("WARNING", "meta", f"unknown key '{key}'"))
    if not isinstance(meta.get("name"), str) or not meta["name"].strip():
        problems.append(Problem("ERROR", "meta", "missing required field 'name'"))
    else:
        name = meta["name"].strip()
    version_value = meta.get("version")
    if version_value is None:
        problems.append(Problem("ERROR", "meta", "missing required field 'version'"))
    elif isinstance(version_value, bool) or not isinstance(version_value, int) or version_value < 1:
        problems.append(Problem("ERROR", "meta", "'version' must be a whole number, 1 or more"))
    else:
        version = version_value
    source_value = meta.get("source")
    if source_value is None:
        problems.append(Problem("WARNING", "meta", "no 'source' URL: say where these rules come from"))
    elif not isinstance(source_value, str) or not re.match(r"^https?://\S+$", source_value):
        problems.append(Problem("ERROR", "meta", "'source' must be an http(s) URL"))
    else:
        source = source_value
    rubric_value = meta.get("rubric")
    if rubric_value is not None:
        valid = isinstance(rubric_value, list) and all(
            isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and row[0].strip() and _is_number(row[1]) and row[1] > 0
            for row in rubric_value
        )
        if valid:
            rubric = [(row[0], float(row[1])) for row in rubric_value]
        else:
            problems.append(Problem("ERROR", "meta", "'rubric' must be a list of [\"criterion\", weight] pairs"))
    return name, version, source, rubric


def _validate_check(raw: dict[str, Any], index: int, seen_ids: set[str], problems: list[Problem]) -> CheckSpec | None:
    check_id = raw.get("id")
    where = f"check '{check_id}'" if isinstance(check_id, str) and check_id else f"check #{index}"
    errors_before = sum(1 for p in problems if p.level == "ERROR")

    def error(message: str) -> None:
        problems.append(Problem("ERROR", where, message))

    if not isinstance(check_id, str) or not check_id:
        error("missing required field 'id'")
    elif not ID_RE.match(check_id) or len(check_id) > MAX_ID_LEN:
        error(f"id must be kebab-case (a-z, 0-9, '-') and at most {MAX_ID_LEN} characters")
    elif check_id in seen_ids:
        error("duplicate id")
    else:
        seen_ids.add(check_id)
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        error("missing required field 'title'")
    type_name = raw.get("type")
    ctype = CHECK_TYPES.get(type_name) if isinstance(type_name, str) else None
    if type_name is None:
        error("missing required field 'type'")
    elif ctype is None:
        error(f"unknown check type '{type_name}' (known: {', '.join(CHECK_TYPES)})")
    required = raw.get("required", True)
    if not isinstance(required, bool):
        error("'required' must be true or false")
        required = True
    hint = raw.get("fix_hint")
    if hint is None:
        problems.append(Problem("WARNING", where, "no 'fix_hint' for the fixer agent"))
        hint = ""
    elif not isinstance(hint, str):
        error("'fix_hint' must be text")
        hint = ""

    params = {k: v for k, v in raw.items() if k not in CHECK_KEYS}
    if ctype is not None:
        for key, value in params.items():
            param = ctype.params.get(key)
            if param is None:
                allowed = ", ".join(ctype.params) or "none"
                error(f"unknown parameter '{key}' for type '{ctype.name}' (allowed: {allowed})")
            elif (message := _check_value(param.kind, value, param)) is not None:
                error(f"parameter '{key}' {message}")
        for key, param in ctype.params.items():
            if param.required and key not in params:
                error(f"missing required parameter '{key}' for type '{ctype.name}'")
        if ctype.runs_code:
            problems.append(
                Problem("WARNING", where, f"type 'command' runs code on this machine ({params.get('cmd')!r}); a person must approve it")
            )
    if sum(1 for p in problems if p.level == "ERROR") > errors_before:
        return None
    return CheckSpec(check_id, title.strip(), type_name, required, hint, params)


def parse_ruleset(path: Path | str) -> tuple[Ruleset | None, list[Problem]]:
    """Parse a ruleset file. The Ruleset is None when any ERROR was found."""
    path = Path(path)
    problems: list[Problem] = []
    raw = _read_raw(path, 0, problems)
    if raw is None:
        return None, problems
    meta, raw_checks = raw
    name, version, source, rubric = _validate_meta(meta, problems)
    seen: set[str] = set()
    checks: list[CheckSpec] = []
    valid = True
    for index, entry in enumerate(raw_checks, start=1):
        spec = _validate_check(entry, index, seen, problems)
        if spec is None:
            valid = False
        else:
            checks.append(spec)
    if not raw_checks:
        problems.append(Problem("ERROR", "file", "no [[check]] entries"))
    elif valid and not any(c.required for c in checks):
        problems.append(Problem("WARNING", "file", "no required checks: nothing can ever block the verdict"))
    if any(p.level == "ERROR" for p in problems):
        return None, problems
    return Ruleset(name, version, source, rubric, checks, str(path)), problems


def load_ruleset(path: Path | str) -> Ruleset:
    """Parse a ruleset or raise RulesetError listing every error."""
    ruleset, problems = parse_ruleset(path)
    if ruleset is None:
        raise RulesetError([p for p in problems if p.level == "ERROR"])
    return ruleset
