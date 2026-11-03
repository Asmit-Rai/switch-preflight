"""links_ok: every http(s) link in the README (and extra files) answers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import net
from ..markdown import extract_links, is_placeholder_host
from ..model import Outcome, failed, passed, skipped
from .base import MAX_DOC_BYTES, CheckType, Context, Param, clip, find_file, read_text, shorten

LINK_TIMEOUT = 10.0
WORKERS = 8
# Sites that block scripts answer these even when the page is fine in a browser.
BLOCKED_STATUSES = (401, 403, 429, 999)


def _probe(ctx: Context, url: str) -> tuple[str, str]:
    """Return (kind, reason). kind is ok, dead, blocked or unreachable.

    HEAD first, then GET: some servers refuse HEAD but serve the page.
    """
    last = ("unreachable", "no answer")
    for method in ("HEAD", "GET"):
        try:
            response = ctx.fetch(url, method=method, timeout=LINK_TIMEOUT, max_bytes=0)
        except net.NetError as exc:
            last = ("unreachable", exc.reason)
            continue
        if 200 <= response.status < 400:
            return ("ok", f"HTTP {response.status}")
        if response.status in BLOCKED_STATUSES:
            last = ("blocked", f"HTTP {response.status}, the site may block scripts")
        else:
            last = ("dead", f"HTTP {response.status}")
    return last


def links_ok(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    links: dict[str, None] = {}
    for name in [p["file"], *p["extra_files"]]:
        path = find_file(ctx.project, name)
        if path is None:
            return failed(f"{name} not found")
        text = read_text(path, MAX_DOC_BYTES)
        if text is None:
            return skipped(f"{name} is binary or too large to read")
        for url in extract_links(text):
            links.setdefault(url, None)

    ignore = [re.compile(rx) for rx in p["ignore"]]
    wanted = [u for u in links if not any(rx.search(u) for rx in ignore)]
    ignored = len(links) - len(wanted)
    note = f" ({ignored} ignored by the ruleset)" if ignored else ""
    if not wanted:
        return passed("no links to check" + note)

    placeholders = [u for u in wanted if is_placeholder_host(u)]
    real = [u for u in wanted if u not in placeholders]
    problems = [f"{clip(u, 60)} (placeholder host)" for u in placeholders]  # known without a network
    if ctx.offline:
        if problems:
            return failed(f"{len(problems)} bad link(s): {shorten(problems, 3)}")
        return skipped(f"offline mode: {len(real)} link(s) not checked")

    to_check = real[: int(p["max_links"])]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        probed = list(pool.map(lambda u: _probe(ctx, u), to_check))
    online = any(kind != "unreachable" for kind, _ in probed)  # at least one link answered

    unverified: list[str] = []
    for url, (kind, reason) in zip(to_check, probed):
        if kind == "dead" or (kind == "unreachable" and online):
            problems.append(f"{clip(url, 60)} ({reason})")
        elif kind in ("blocked", "unreachable"):
            unverified.append(f"{clip(url, 60)} ({reason})")
    if problems:
        return failed(f"{len(problems)} bad link(s): {shorten(problems, 3)}")
    if unverified:
        return skipped(f"cannot verify {len(unverified)} link(s): {shorten(unverified, 2)}")
    if len(real) > len(to_check):
        return skipped(
            f"{len(real)} links found but max_links is {len(to_check)}; {len(real) - len(to_check)} not checked"
        )
    return passed(f"{len(to_check)} link(s) OK" + note)


LINKS_OK = CheckType(
    "links_ok",
    links_ok,
    {
        "file": Param("relpath", default="README.md"),
        "extra_files": Param("relpaths", default=[]),
        "max_links": Param("int", default=40, minimum=1),
        "ignore": Param("regexes", default=[]),
    },
    "Every http(s) link in `file` (and `extra_files`) answers 2xx/3xx within 10 s (HEAD, then GET). "
    "localhost, example.com and .invalid hosts are placeholders and fail. `ignore` lists URL regexes to skip.",
    network=True,
)
