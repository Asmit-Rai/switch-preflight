"""Shared pieces for check types: run context, registry records and small helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import gitutil, net
from ..model import Outcome

MAX_TEXT_BYTES = 1_000_000  # text checks skip files bigger than this
MAX_DOC_BYTES = 5_000_000  # but a README or other named file may be larger


@dataclass
class Context:
    """Everything a check may use. Tests build one with a fake `fetch` or `env`."""

    project: Path
    offline: bool = False
    fetch: net.Fetch = net.fetch
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def tracked_files(self) -> list[str]:
        if "tracked" not in self._cache:
            self._cache["tracked"] = gitutil.tracked_files(self.project)
        return self._cache["tracked"]

    def project_files(self) -> list[str]:
        if "present" not in self._cache:
            self._cache["present"] = gitutil.project_files(self.project)
        return self._cache["present"]


@dataclass(frozen=True)
class Param:
    """One parameter of a check type. `kind` drives validation in `ruleset.py`."""

    kind: str  # str globs headings regex regexes relpath relpaths flags bool int number timestamp
    required: bool = False
    default: Any = None
    minimum: float | None = None


@dataclass(frozen=True)
class CheckType:
    """A registered check type: its code, its parameters and a one-line summary."""

    name: str
    run: Callable[[Context, Mapping[str, Any]], Outcome]
    params: Mapping[str, Param]
    summary: str
    network: bool = False  # SKIPs under --offline (at least in part)
    runs_code: bool = False  # executes a shell command: a person must approve it


def glob_regex(pattern: str) -> re.Pattern[str]:
    """Shell-style glob, case-insensitive. `*` and `?` stay inside one path segment, `**` crosses."""
    pattern = pattern.replace("\\", "/").removeprefix("./")
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                i += 2
                if pattern[i : i + 1] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[" and (end := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("".join(out) + r"\Z", re.IGNORECASE)


def path_matches(pattern: str, path: str, any_depth: bool = False) -> bool:
    """Does the project-relative `path` match the glob?

    With any_depth, a pattern without `/` (like `.env` or `*.pem`) matches the file name
    at any depth, the way .gitignore does.
    """
    target = path
    if any_depth and "/" not in pattern.replace("\\", "/"):
        target = path.rsplit("/", 1)[-1]
    return glob_regex(pattern).match(target) is not None


def find_file(project: Path, name: str) -> Path | None:
    """Locate a file under the project; the last path segment is matched case-insensitively."""
    target = project / name
    if target.is_file():
        return target
    if target.parent.is_dir():
        wanted = target.name.lower()
        for entry in sorted(target.parent.iterdir()):
            if entry.name.lower() == wanted and entry.is_file():
                return entry
    return None


def read_text(path: Path, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
    """File content as text, or None when it is binary or bigger than max_bytes.

    Raises OSError when the file cannot be read.
    """
    if path.stat().st_size > max_bytes:
        return None
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def regex_flags(letters: str) -> int:
    """Translate flags like "im" into re flags (i=ignore case, m=multiline, s=dotall)."""
    flags = 0
    for letter in letters or "":
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}[letter]
    return flags


def line_of(text: str, offset: int) -> int:
    """1-based line number of a character offset."""
    return text.count("\n", 0, offset) + 1


def shorten(items: Sequence[str], limit: int = 4) -> str:
    """Join the first `limit` items and say how many were left out."""
    shown = ", ".join(items[:limit])
    extra = len(items) - limit
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def clip(text: str, width: int) -> str:
    """Cut text to `width` characters, ending with `...` when something was removed."""
    return text if len(text) <= width else text[: max(width - 3, 0)] + "..."
