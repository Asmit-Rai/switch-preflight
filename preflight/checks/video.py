"""video_link_present and video_max_seconds: the demo video exists and is short enough."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .. import net
from ..markdown import extract_links, link_host
from ..model import Outcome, failed, passed, skipped
from .base import MAX_DOC_BYTES, CheckType, Context, Param, clip, find_file, read_text

VIDEO_PATTERN = r"youtube\.com|youtu\.be|vimeo\.com|loom\.com"
_LENGTH = re.compile(r'"lengthSeconds"\s*:\s*"(\d+)"')
_ISO_DURATION = re.compile(r'itemprop="duration"\s+content="PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"')
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def find_video_links(ctx: Context, name: str, pattern: str) -> list[str] | str:
    """Video links in the README, or an error message when it cannot be read."""
    path = find_file(ctx.project, name)
    if path is None:
        return f"{name} not found"
    text = read_text(path, MAX_DOC_BYTES)
    if text is None:
        return f"{name} is binary or too large to read"
    return [u for u in extract_links(text) if re.search(pattern, u, re.IGNORECASE)]


def video_link_present(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    found = find_video_links(ctx, p["file"], p["pattern"])
    if isinstance(found, str):
        return failed(found)
    if found:
        return passed(f"video link: {clip(found[0], 70)}")
    return failed(f"no video link (YouTube, Vimeo or Loom) in {p['file']}")


def youtube_id(url: str) -> str | None:
    """The 11-character video id of a YouTube watch, share, embed or shorts URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    segments = [s for s in parts.path.split("/") if s]
    candidate = None
    if host == "youtu.be" and segments:
        candidate = segments[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if segments and segments[0] == "watch":
            candidate = (parse_qs(parts.query).get("v") or [None])[0]
        elif len(segments) >= 2 and segments[0] in ("embed", "shorts", "live", "v"):
            candidate = segments[1]
    return candidate if candidate and _VIDEO_ID.match(candidate) else None


def parse_duration(page: str) -> int | None:
    """Seconds from a YouTube watch page, or None when the page does not say."""
    match = _LENGTH.search(page)
    if match:
        return int(match.group(1))
    meta = _ISO_DURATION.search(page)
    if meta and any(meta.groups()):
        hours, minutes, seconds = (int(g or 0) for g in meta.groups())
        return hours * 3600 + minutes * 60 + seconds
    return None


def _clock(seconds: float) -> str:
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _verdict(seconds: float, limit: float, source: str) -> Outcome:
    shown = f"{_clock(seconds)} ({seconds:g} s)"
    if seconds <= limit:
        return passed(f"{source} is {shown}, limit {limit:g} s")
    return failed(f"{source} is {shown}, over the {limit:g} s limit")


def _ffprobe_seconds(ctx: Context, file: str) -> float | Outcome:
    """Duration in seconds, or a SKIP outcome saying why it could not be read."""
    path = find_file(ctx.project, file)
    if path is None:
        return skipped(f"local video {file} not found")
    ffprobe = shutil.which("ffprobe", path=ctx.env.get("PATH"))
    if ffprobe is None:
        return skipped("ffprobe not installed")
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30, env=dict(ctx.env),
        )
        return float(proc.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return skipped("ffprobe could not read the duration; check by hand")


def video_max_seconds(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    limit = float(p["max_seconds"])
    if p["local_file"]:
        probed = _ffprobe_seconds(ctx, p["local_file"])
        if isinstance(probed, Outcome):
            return probed
        return _verdict(probed, limit, p["local_file"])

    found = find_video_links(ctx, p["file"], p["pattern"])
    if isinstance(found, str):
        return skipped(found)
    if not found:
        return skipped(f"no video link in {p['file']}, so there is no duration to check")
    url = found[0]
    video = youtube_id(url)
    if video is None:
        return skipped(f"cannot read duration of {link_host(url)} videos; check by hand")
    if ctx.offline:
        return skipped("offline mode: cannot read the video duration; check by hand")
    headers = {"Accept-Language": "en", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
    try:
        page = ctx.fetch(f"https://www.youtube.com/watch?v={video}", headers=headers, timeout=15.0)
    except net.NetError as exc:
        return skipped(f"cannot read duration ({exc.reason}); check by hand")
    seconds = parse_duration(page.text()) if page.status == 200 else None
    if seconds is None:
        return skipped("cannot read duration; check by hand")
    return _verdict(seconds, limit, "video")


VIDEO_LINK_PRESENT = CheckType(
    "video_link_present",
    video_link_present,
    {"file": Param("relpath", default="README.md"), "pattern": Param("regex", default=VIDEO_PATTERN)},
    "README contains a link matching `pattern` (default: YouTube, Vimeo or Loom).",
)
VIDEO_MAX_SECONDS = CheckType(
    "video_max_seconds",
    video_max_seconds,
    {
        "max_seconds": Param("number", required=True, minimum=1),
        "file": Param("relpath", default="README.md"),
        "pattern": Param("regex", default=VIDEO_PATTERN),
        "local_file": Param("relpath"),
    },
    "The README's first video is at most `max_seconds` long. Reads YouTube watch pages; or set `local_file` "
    "to use ffprobe. Anything it cannot read is SKIP, never a guess.",
    network=True,
)
