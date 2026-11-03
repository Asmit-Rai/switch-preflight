"""Render a verdict document as a table, a compact chat message, or JSON."""

from __future__ import annotations

import json
import textwrap
from typing import Any

from .checks.base import clip
from .gitutil import short_sha
from .model import PASS, READY

TABLE_WIDTH = 100
ID_WIDTH_MAX = 32
CHAT_LIMIT = 1800  # one Discord message is 2000 characters; leave room for the code fence
CHAT_LINE_MAX = 92
CHAT_PASS_MAX = 150
CHAT_BLOCKING_MAX = 380


def summary_line(doc: dict[str, Any]) -> str:
    s = doc["summary"]
    return f"SUMMARY: {s['pass']} pass, {s['fail']} fail, {s['warn']} warn, {s['skip']} skip"


def verdict_line(doc: dict[str, Any], max_blocking_chars: int | None = None) -> str:
    """`VERDICT: READY` or `VERDICT: NOT READY (blocking: id, id)`."""
    if doc["verdict"] == READY:
        return "VERDICT: READY"
    ids: list[str] = list(doc["blocking"])
    text = ", ".join(ids)
    if max_blocking_chars is not None and len(text) > max_blocking_chars:
        shown: list[str] = []
        for i, item in enumerate(ids):
            candidate = ", ".join([*shown, item])
            if len(candidate) + len(f", +{len(ids) - i} more") > max_blocking_chars:
                break
            shown.append(item)
        text = ", ".join(shown) + f", +{len(ids) - len(shown)} more"
    return f"VERDICT: NOT READY (blocking: {text})"


def _pass_line(passes: list[str]) -> str:
    """`PASS (n): id, id, +k more`: whole ids only, at most CHAT_PASS_MAX characters."""
    prefix = f"PASS ({len(passes)}): "
    shown: list[str] = []
    for i, ident in enumerate(passes):
        left = len(passes) - i - 1
        tail = f", +{left} more" if left else ""
        if len(prefix + ", ".join([*shown, ident]) + tail) > CHAT_PASS_MAX:
            break
        shown.append(ident)
    if not shown:
        return f"PASS ({len(passes)})"
    extra = len(passes) - len(shown)
    return prefix + ", ".join(shown) + (f", +{extra} more" if extra else "")


def format_json(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2)


def format_table(doc: dict[str, Any]) -> str:
    """The full report, fixed width (every line is at most 100 columns, except the final verdict line)."""
    rules = doc["ruleset"]
    results = doc["results"]
    lines = [clip(f"PREFLIGHT  {rules['name']} (ruleset v{rules['version']})", TABLE_WIDTH)]
    commit = short_sha(doc["commit"])
    one_line = f"project: {doc['project']}   commit: {commit}   run: {doc['created_at']}"
    if len(one_line) <= TABLE_WIDTH:
        lines.append(one_line)
    else:
        path = doc["project"]
        room = TABLE_WIDTH - len("project: ")
        lines.append("project: " + (path if len(path) <= room else "..." + path[-(room - 3):]))
        lines.append(f"commit: {commit}   run: {doc['created_at']}")

    num_w = max(2, len(str(len(results))))
    id_w = min(max([len("ID"), *(len(r["id"]) for r in results)]), ID_WIDTH_MAX)
    head = f" {'#':>{num_w}}  {'ID':<{id_w}}  {'REQ':<3}  {'STATUS':<6}  "
    lines.append(head + "DETAIL")
    detail_w = max(TABLE_WIDTH - len(head), 20)
    for n, row in enumerate(results, start=1):
        prefix = (
            f" {n:>{num_w}}  {clip(row['id'], id_w):<{id_w}}  "
            f"{'yes' if row['required'] else 'no':<3}  {row['status']:<6}  "
        )
        wrapped = textwrap.wrap(row["detail"], width=detail_w, break_long_words=True, break_on_hyphens=False) or [""]
        lines.append(prefix + wrapped[0])
        lines.extend(" " * len(prefix) + part for part in wrapped[1:])
    lines.append(summary_line(doc))
    lines.append(verdict_line(doc))
    return "\n".join(lines)


def format_chat(doc: dict[str, Any], limit: int = CHAT_LIMIT) -> str:
    """A compact report that fits one Discord message (at most `limit` characters).

    Blocking checks come first, then optional checks that need attention, then one line for
    the passes. If it does not fit, the tail is cut and a `(+N more ...)` line says so.
    """
    rules = doc["ruleset"]
    head = clip(f"PREFLIGHT {rules['name']} (v{rules['version']}) @ {short_sha(doc['commit'])}", 100)
    footer = [summary_line(doc), verdict_line(doc, CHAT_BLOCKING_MAX)]

    def line(row: dict[str, Any]) -> tuple[str, int]:
        return clip(f"{row['status']} {row['id']}: {row['detail']}", CHAT_LINE_MAX), 1

    results = doc["results"]
    blocking = [r for r in results if r["required"] and r["status"] != PASS]
    optional = [r for r in results if not r["required"] and r["status"] != PASS]
    passes = [r["id"] for r in results if r["status"] == PASS]
    body = [line(r) for r in blocking + optional]
    if passes:
        body.append((_pass_line(passes), len(passes)))

    def assemble(shown: int) -> str:
        hidden = sum(count for _, count in body[shown:])
        lines = [head, *(text for text, _ in body[:shown])]
        if hidden:
            lines.append(f"(+{hidden} more, run with --format table)")
        return "\n".join([*lines, *footer])

    for shown in range(len(body), -1, -1):
        text = assemble(shown)
        if len(text) <= limit:
            return text
    return text[:limit]
