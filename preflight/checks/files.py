"""Check types about files: exists, absent, size, contents."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from ..model import Outcome, failed, passed, skipped
from .base import (
    MAX_DOC_BYTES,
    CheckType,
    Context,
    Param,
    clip,
    find_file,
    line_of,
    path_matches,
    read_text,
    regex_flags,
    shorten,
)


def file_exists(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    files = ctx.project_files()
    for pattern in p["paths"]:
        hits = [f for f in files if path_matches(pattern, f)]
        if hits:
            return passed(f"found {hits[0]}")
    return failed("no file matching " + ", ".join(p["paths"]))


def file_absent(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    tracked = ctx.tracked_files()
    hits = [f for f in tracked if any(path_matches(g, f, any_depth=True) for g in p["paths"])]
    if hits:
        return failed(f"tracked in git: {shorten(hits)}")
    return passed("no tracked file matches " + ", ".join(p["paths"]))


def file_contains(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    path = find_file(ctx.project, p["file"])
    if path is None:
        return failed(f"{p['file']} not found")
    text = read_text(path, MAX_DOC_BYTES)
    if text is None:
        return skipped(f"{p['file']} is binary or too large to read")
    if re.search(p["pattern"], text, regex_flags(p["flags"])):
        return passed(f"pattern found in {path.name}")
    return failed(f"pattern /{clip(p['pattern'], 40)}/ not found in {path.name}")


def text_absent(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    regex = re.compile(p["pattern"], regex_flags(p["flags"]))
    globs = p["globs"]
    hits: list[str] = []
    scanned = unreadable = 0
    for rel in ctx.tracked_files():
        if globs and not any(path_matches(g, rel, any_depth=True) for g in globs):
            continue
        path = ctx.project / rel
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = read_text(path)
        except OSError:
            unreadable += 1
            continue
        if text is None:  # binary or over 1 MB
            continue
        scanned += 1
        for match in list(regex.finditer(text))[:20]:
            hits.append(f"{rel}:{line_of(text, match.start())}")
    if hits:
        return failed(f"{len(hits)} match(es): {shorten(hits, 5)}")
    note = f" ({unreadable} unreadable file(s) skipped)" if unreadable else ""
    return passed(f"no match in {scanned} tracked text file(s){note}")


def max_file_mb(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    limit = float(p["limit_mb"])
    too_big: list[tuple[str, int]] = []
    checked = 0
    for rel in ctx.tracked_files():
        try:
            size = os.lstat(ctx.project / rel).st_size
        except OSError:
            continue  # tracked but missing on disk: not a size problem
        checked += 1
        if size > limit * 1024 * 1024:
            too_big.append((rel, size))
    if too_big:
        too_big.sort(key=lambda item: -item[1])
        listing = [f"{rel} ({size / 1024 / 1024:.1f} MB)" for rel, size in too_big]
        return failed(f"{len(too_big)} file(s) over {limit:g} MB: {shorten(listing, 3)}")
    return passed(f"no tracked file over {limit:g} MB ({checked} checked)")


FILE_EXISTS = CheckType(
    "file_exists",
    file_exists,
    {"paths": Param("globs", required=True)},
    "Any of `paths` (globs, relative to the project) matches a file that is tracked or untracked-but-present.",
)
FILE_ABSENT = CheckType(
    "file_absent",
    file_absent,
    {"paths": Param("globs", required=True)},
    "None of `paths` matches a git-tracked file. A pattern without `/` matches at any depth.",
)
FILE_CONTAINS = CheckType(
    "file_contains",
    file_contains,
    {
        "file": Param("relpath", required=True),
        "pattern": Param("regex", required=True),
        "flags": Param("flags", default=""),
    },
    "Regex `pattern` is found in `file`. Optional `flags`: letters from i, m, s.",
)
TEXT_ABSENT = CheckType(
    "text_absent",
    text_absent,
    {
        "pattern": Param("regex", required=True),
        "globs": Param("globs", default=[]),
        "flags": Param("flags", default=""),
    },
    "Regex `pattern` matches no tracked text file (binary and over-1-MB files are skipped). `globs` limits the scan.",
)
MAX_FILE_MB = CheckType(
    "max_file_mb",
    max_file_mb,
    {"limit_mb": Param("number", required=True, minimum=0.01)},
    "No git-tracked file is larger than `limit_mb` megabytes.",
)
