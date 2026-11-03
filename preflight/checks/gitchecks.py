"""Check types about git: first commit date, clean tree, public remote."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .. import gitutil, net
from ..model import Outcome, failed, passed, skipped
from ..timeutil import iso_utc, parse_timestamp
from .base import CheckType, Context, Param, shorten

_GITHUB_REMOTE = re.compile(
    r"^(?:https?://(?:[^@/\s]+@)?github\.com/|git@github\.com:|ssh://(?:[^@/\s]+@)?github\.com/"
    r"|git://github\.com/)(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_USERINFO = re.compile(r"(://)[^@/\s]+@")


def parse_github_remote(url: str) -> tuple[str, str] | None:
    """(owner, repo) from a github.com remote in https or ssh form, else None."""
    match = _GITHUB_REMOTE.match(url.strip())
    return (match.group("owner"), match.group("name")) if match else None


def redact_url(url: str) -> str:
    """Drop `user:password@` from a URL so credentials never reach a report."""
    return _USERINFO.sub(r"\1", url)


def git_first_commit_after(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    start = parse_timestamp(p["after"])
    commits = gitutil.commit_times(ctx.project)
    if not commits:
        return failed("repository has no commits")
    sha, earliest = min(commits, key=lambda c: (c[1], c[0]))
    if earliest >= start:
        return passed(f"earliest commit {iso_utc(earliest)} ({gitutil.short_sha(sha)}) is after {iso_utc(start)}")
    early = sum(1 for _, when in commits if when < start)
    return failed(
        f"earliest commit {iso_utc(earliest)} ({gitutil.short_sha(sha)}) is before the start "
        f"{iso_utc(start)}; {early} of {len(commits)} commit(s) are earlier"
    )


def git_clean(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    entries = gitutil.dirty_entries(ctx.project)
    if not entries:
        return passed("working tree is clean")
    return failed(f"{len(entries)} uncommitted change(s): {shorten(entries)}")


def git_remote_public(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    url = gitutil.origin_url(ctx.project)
    if url is None:
        return failed("no 'origin' remote is configured")
    parsed = parse_github_remote(url)
    if parsed is None:
        return failed(f"origin is not a github.com URL: {redact_url(url)}")
    owner, name = parsed
    label = f"github.com/{owner}/{name}"
    if ctx.offline:
        return skipped(f"offline mode: cannot ask GitHub whether {label} is public")

    api = f"https://api.github.com/repos/{owner}/{name}"
    headers = {"Accept": "application/vnd.github+json"}
    token = ctx.env.get("GITHUB_TOKEN")
    try:
        if token:
            response = ctx.fetch(api, headers={**headers, "Authorization": f"Bearer {token}"}, timeout=10.0)
            if response.status == 401:  # a stale token must not break a public-repo question
                response = ctx.fetch(api, headers=headers, timeout=10.0)
        else:
            response = ctx.fetch(api, headers=headers, timeout=10.0)
    except net.NetError as exc:
        return skipped(f"cannot reach api.github.com ({exc.reason}); check by hand")

    if response.status == 200:
        try:
            private = json.loads(response.text()).get("private")
        except (ValueError, AttributeError):
            return skipped("GitHub sent an unreadable answer; check by hand")
        if private is False:
            return passed(f"{label} is public")
        if private is True:
            return failed(f"{label} is private")
        return skipped("GitHub's answer has no 'private' field; check by hand")
    if response.status == 404:
        return failed(f"{label} not found or private")
    if response.status in (403, 429):
        return skipped(f"GitHub API refused (HTTP {response.status}, rate limit?); set GITHUB_TOKEN or retry later")
    return skipped(f"GitHub API answered HTTP {response.status}; retry later")


GIT_FIRST_COMMIT_AFTER = CheckType(
    "git_first_commit_after",
    git_first_commit_after,
    {"after": Param("timestamp", required=True)},
    "The earliest author date of every commit in `git log --all` is at or after `after` (ISO 8601, e.g. 2026-11-03T18:31:00Z).",
)
GIT_CLEAN = CheckType(
    "git_clean",
    git_clean,
    {},
    "No uncommitted or untracked changes (the .preflight/ folder is ignored).",
)
GIT_REMOTE_PUBLIC = CheckType(
    "git_remote_public",
    git_remote_public,
    {},
    "`origin` is a github.com URL and GitHub's API says the repo is public. Uses GITHUB_TOKEN if set. "
    "404 fails; rate limits and network errors SKIP.",
    network=True,
)
