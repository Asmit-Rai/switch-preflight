"""The submission pack: a Markdown summary that is only written while the gate is open."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

from . import gitutil
from .checks.base import find_file, read_text
from .checks.gitchecks import parse_github_remote, redact_url
from .checks.video import VIDEO_PATTERN
from .markdown import extract_links, parse_headings


def read_meta(path: Path) -> dict[str, Any]:
    """Optional project facts: a `[project]` table with name, team, description, repo_url, video_url."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    if not isinstance(project, dict):
        raise ValueError("[project] must be a table")
    return {k: str(v) for k, v in project.items() if isinstance(v, (str, int, float))}


def read_readme(project: Path) -> str:
    """The project's README.md as text, or "" when there is none or it cannot be read."""
    path = find_file(project, "README.md")
    if path is None:
        return ""
    try:
        return read_text(path, 5_000_000) or ""
    except OSError:
        return ""


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def repo_url(project: Path, meta: dict[str, Any]) -> str:
    if meta.get("repo_url"):
        return meta["repo_url"]
    origin = gitutil.origin_url(project)
    if origin is None:
        return "(no origin remote)"
    parsed = parse_github_remote(origin)
    return f"https://github.com/{parsed[0]}/{parsed[1]}" if parsed else redact_url(origin)


def commit_url(project: Path, commit: str | None) -> str | None:
    """`https://github.com/<owner>/<repo>/commit/<sha>`, or None when the commit may not be on GitHub yet.

    A link only when origin is a github.com remote and the commit is on a remote-tracking branch.
    Reads local refs only (no network), so the caller shows "not pushed yet" for None.
    """
    if not commit:
        return None
    origin = gitutil.origin_url(project)
    parsed = parse_github_remote(origin) if origin else None
    if parsed is None or not gitutil.on_remote_branch(project, commit):
        return None
    return f"https://github.com/{parsed[0]}/{parsed[1]}/commit/{commit}"


def video_url(readme: str, meta: dict[str, Any]) -> str:
    if meta.get("video_url"):
        return meta["video_url"]
    for url in extract_links(readme):
        if re.search(VIDEO_PATTERN, url, re.IGNORECASE):
            return url
    return "(no video link found in README)"


def project_name(project: Path, readme: str, meta: dict[str, Any]) -> str:
    if meta.get("name"):
        return meta["name"]
    for heading in parse_headings(readme):
        if heading.level == 1:
            return heading.text
    return project.resolve().name


def build_pack(project: Path, doc: dict[str, Any], meta: dict[str, Any]) -> str:
    """Markdown for a verified project. `doc` must come from an open gate."""
    readme = read_readme(project)
    rules = doc["ruleset"]
    summary = doc["summary"]
    commit = doc["commit"]
    link = commit_url(project, commit)
    rows = [
        ("Project", project_name(project, readme, meta)),
        *([("Team", meta["team"])] if meta.get("team") else []),
        *([("About", meta["description"])] if meta.get("description") else []),
        ("Repository", repo_url(project, meta)),
        ("Demo video", video_url(readme, meta)),
        ("Commit", f"[`{gitutil.short_sha(commit)}`]({link}) `{commit}`" if link else f"`{commit}`"),
        ("Verified at", doc["created_at"]),
        ("Ruleset", f"{rules['name']} v{rules['version']}" + (f" ({rules['source']})" if rules["source"] else "")),
        ("Verdict", "**READY**"),
    ]
    lines = [f"# Submission pack: {_cell(rows[0][1])}", "", "| | |", "|---|---|"]
    lines += [f"| {key} | {_cell(value)} |" for key, value in rows]
    lines += ["", "## Checks", "", "| # | ID | Required | Status | Detail |", "|---|---|---|---|---|"]
    for n, row in enumerate(doc["results"], start=1):
        lines.append(
            f"| {n} | `{row['id']}` | {'yes' if row['required'] else 'no'} | {row['status']} | {_cell(row['detail'])} |"
        )
    lines += [
        "",
        f"Summary: {summary['pass']} pass, {summary['fail']} fail, {summary['warn']} warn, {summary['skip']} skip.",
        "",
        "_Written by Preflight, and only while `python3 -m preflight verdict` reports GATE OK for this commit._",
        "",
    ]
    return "\n".join(lines)


def write_pack(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)
