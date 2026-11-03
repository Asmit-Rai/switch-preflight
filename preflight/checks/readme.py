"""readme_headings: the README has the sections the rules ask for."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..markdown import parse_headings
from ..model import Outcome, failed, passed, skipped
from .base import MAX_DOC_BYTES, CheckType, Context, Param, find_file, read_text


def readme_headings(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    path = find_file(ctx.project, p["file"])
    if path is None:
        return failed(f"{p['file']} not found")
    text = read_text(path, MAX_DOC_BYTES)
    if text is None:
        return skipped(f"{path.name} is binary or too large to read")
    headings = parse_headings(text)
    min_chars = int(p["min_chars"])
    missing: list[str] = []
    thin: list[str] = []
    for spec in p["headings"]:
        # "a|b" accepts either wording; the first one is what the report names.
        options = [o.strip().lower() for o in spec.split("|") if o.strip()]
        label = spec.split("|")[0].strip()
        found = [h for h in headings if any(o in h.text.lower() for o in options)]
        if not found:
            missing.append(label)
        elif min_chars:
            longest = max(len(" ".join(h.body.split())) for h in found)
            if longest < min_chars:
                thin.append(f"{label} ({longest}/{min_chars} chars)")
    if missing or thin:
        parts = []
        if missing:
            parts.append("missing heading(s): " + ", ".join(missing))
        if thin:
            parts.append("section too short: " + ", ".join(thin))
        return failed("; ".join(parts))
    return passed(f"all {len(p['headings'])} headings present in {path.name}")


README_HEADINGS = CheckType(
    "readme_headings",
    readme_headings,
    {
        "headings": Param("headings", required=True),
        "file": Param("relpath", default="README.md"),
        "min_chars": Param("int", default=0, minimum=0),
    },
    "README (`file`, default README.md, case-insensitive lookup) has a markdown heading containing each "
    "string in `headings` (case-insensitive; `a|b` accepts either). Optional `min_chars` of body text under each.",
)
