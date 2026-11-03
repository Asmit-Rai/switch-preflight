"""Tiny markdown helpers: headings and links. Only what the checks need."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_URL = re.compile(r"https?://[^\s<>\"'`\\]+", re.IGNORECASE)
_INLINE_CODE = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Hosts that only ever appear as placeholders in a README (RFC 2606 names and localhost).
_PLACEHOLDER_HOSTS = ("localhost", "example.com", "example.org", "example.net")
_PLACEHOLDER_TLDS = ("localhost", "example", "invalid", "test")


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line: int  # 1-based
    body: str  # text under it, up to the next heading of the same or a higher level


def _fence_closes(line: str, char: str, size: int) -> bool:
    stripped = line.strip()
    return len(stripped) >= size and stripped == char * len(stripped)


def blank_code(text: str) -> str:
    """Blank out fenced code, inline code and HTML comments, keeping line numbers."""
    out: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        if fence is not None:
            if _fence_closes(line, *fence):
                fence = None
            out.append("")
            continue
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            fence = (marker[0], len(marker))
            out.append("")
            continue
        out.append(_INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line))
    cleaned = "\n".join(out)
    return _HTML_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), cleaned)


def parse_headings(text: str) -> list[Heading]:
    """ATX headings (`# Title` to `###### Title`) outside fenced code blocks."""
    lines = text.splitlines()
    found: list[tuple[int, str, int]] = []  # level, text, 0-based line index
    fence: tuple[str, int] | None = None
    for i, line in enumerate(lines):
        if fence is not None:
            if _fence_closes(line, *fence):
                fence = None
            continue
        fenced = _FENCE.match(line)
        if fenced:
            marker = fenced.group(1)
            fence = (marker[0], len(marker))
            continue
        match = _HEADING.match(line)
        if match and match.group(2).strip():
            found.append((len(match.group(1)), match.group(2).strip(), i))
    headings: list[Heading] = []
    for n, (level, title, index) in enumerate(found):
        end = len(lines)
        for later_level, _, later_index in found[n + 1 :]:
            if later_level <= level:
                end = later_index
                break
        body = _HTML_COMMENT.sub("", "\n".join(lines[index + 1 : end]))
        headings.append(Heading(level, title, index + 1, body))
    return headings


def _trim_url(url: str) -> str:
    while url:
        last = url[-1]
        if last in ".,;:!?*'\"":
            url = url[:-1]
        elif last == ")" and url.count("(") < url.count(")"):
            url = url[:-1]
        elif last == "]" and url.count("[") < url.count("]"):
            url = url[:-1]
        else:
            break
    return url


def extract_links(text: str) -> list[str]:
    """Unique http(s) links in reading order, ignoring code blocks and HTML comments."""
    seen: dict[str, None] = {}
    for match in _URL.finditer(blank_code(text)):
        url = _trim_url(html.unescape(match.group(0)))
        if urlsplit(url).hostname:
            seen.setdefault(url, None)
    return list(seen)


def link_host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_placeholder_host(url: str) -> bool:
    """True for localhost, example.com and reserved names such as .invalid."""
    host = link_host(url)
    if not host:
        return False
    if host in _PLACEHOLDER_HOSTS or any(host.endswith("." + h) for h in _PLACEHOLDER_HOSTS):
        return True
    return host.rsplit(".", 1)[-1] in _PLACEHOLDER_TLDS
