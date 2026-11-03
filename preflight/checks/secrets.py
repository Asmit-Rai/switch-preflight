"""secret_scan: look for credentials in tracked files and, optionally, in all of git history.

Findings are always masked. A report shows the file, the line, the pattern name and at most
the first four characters of the value. A full secret is never printed or stored.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import gitutil
from ..model import Outcome, failed, passed
from .base import CheckType, Context, Param, path_matches, read_text, shorten

ALLOW_FILE = ".preflight-allow"
MAX_LINE_CHARS = 20_000  # longer lines (minified bundles) are cut before scanning
GENERIC_LINE_CHARS = 5_000  # the loose assignment rule only looks at the start of a line
MIN_LINE_CHARS = 16  # nothing shorter can hold a credential
HISTORY_TIMEOUT = 120.0
SHOWN_FINDINGS = 4


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]
    show_prefix: bool = True  # the first 4 characters are a well-known prefix, safe to show


def _p(name: str, pattern: str, show_prefix: bool = True) -> SecretPattern:
    return SecretPattern(name, re.compile(pattern), show_prefix)


PATTERNS = (
    _p("AWS access key id", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b"),
    _p("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    _p("GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"),
    _p("Slack token", r"\bxox[baprs]-[A-Za-z0-9-]{10,200}\b"),
    _p("Discord bot token", r"\b[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,40}\b"),
    _p("private key (PEM header)", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----", show_prefix=False),
    _p("sk- API key", r"\bsk-[A-Za-z0-9_-]{20,}"),
    _p("Stripe live key", r"\b[sr]k_live_[A-Za-z0-9]{16,}\b"),
)

# NAME = value, where the name mentions KEY, TOKEN, SECRET or PASSWORD and the value is 20+ characters.
# The name must start at a word boundary so a long line cannot make the regex backtrack.
_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]*?(?:key|token|secret|passw(?:or)?d)[A-Za-z0-9_.-]*"
    r"[\"']?\s*(?::|=>|=)\s*[\"']?(?P<value>[A-Za-z0-9+/=_~.-]{20,})",
    re.IGNORECASE,
)
_REPEATS = re.compile(r"(.)\1{7,}")  # xxxxxxxx or 00000000: a placeholder in docs, not a credential
_PLACEHOLDER = re.compile(r"example|sample|placeholder|changeme|change_me|your[_-]|dummy|fake|redacted|xxxx|insert|replace|_here$", re.I)
_ENTROPY_MIN = 3.5


def mask(value: str, show_prefix: bool = True) -> str:
    """Hide a value: at most its first four characters survive."""
    return (value[:4] if show_prefix else "") + "****"


def redact(text: str) -> str:
    """Mask anything in `text` that matches a known secret pattern."""
    for pattern in PATTERNS:
        text = pattern.regex.sub(lambda m, pat=pattern: mask(m.group(0), pat.show_prefix), text)
    return text


def entropy(value: str) -> float:
    """Shannon entropy in bits per character."""
    counts = Counter(value)
    total = len(value)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def looks_random(value: str) -> bool:
    """A long value with letters and digits, high entropy and no placeholder wording."""
    if len(value) < 20 or _PLACEHOLDER.search(value):
        return False
    if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
        return False
    return entropy(value) >= _ENTROPY_MIN


@dataclass(frozen=True)
class Hit:
    """One masked match on a line. `fingerprint` identifies the value without storing it."""

    pattern: str
    masked: str
    fingerprint: str


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def find_secrets(line: str) -> list[Hit]:
    """All masked secret matches on one line of text."""
    if len(line) < MIN_LINE_CHARS:
        return []
    line = line[:MAX_LINE_CHARS]
    hits: list[Hit] = []
    for pattern in PATTERNS:
        for match in pattern.regex.finditer(line):
            value = match.group(0)
            if pattern.show_prefix and _REPEATS.search(value[4:]):
                continue  # ghp_xxxxxxxx...: an obvious placeholder
            hits.append(Hit(pattern.name, mask(value, pattern.show_prefix), _fingerprint(value)))
    if not hits:  # the loose rule only runs when no precise pattern already explained the line
        for match in _ASSIGNMENT.finditer(line[:GENERIC_LINE_CHARS]):
            value = match.group("value")
            if looks_random(value):
                hits.append(Hit("secret assignment", mask(value, False), _fingerprint(value)))
    return hits


@dataclass
class Allowlist:
    """Findings the Captain accepted, read from `.preflight-allow` at the project root.

    Lines: `path:line`, `path` (a glob, any line) or `re:REGEX` (matched against the source line).
    """

    locations: set[tuple[str, int]] = field(default_factory=set)
    paths: list[str] = field(default_factory=list)
    patterns: list[re.Pattern[str]] = field(default_factory=list)
    invalid: int = 0

    def allows(self, path: str, line: int, text: str) -> bool:
        if (path, line) in self.locations:
            return True
        if any(path_matches(glob, path) for glob in self.paths):
            return True
        return any(rx.search(text) for rx in self.patterns)


def load_allowlist(project: Path) -> Allowlist:
    allow = Allowlist()
    path = project / ALLOW_FILE
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return allow
    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("re:"):
            try:
                allow.patterns.append(re.compile(entry[3:].strip()))
            except re.error:
                allow.invalid += 1
            continue
        where, sep, tail = entry.rpartition(":")
        if sep and where and tail.strip().isdigit():
            allow.locations.add((where.strip().replace("\\", "/").removeprefix("./"), int(tail)))
        else:
            allow.paths.append(entry)
    return allow


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    hit: Hit
    commit: str | None = None  # set for findings that exist only in git history

    def label(self) -> str:
        where = f"{self.path}:{self.line} {self.hit.pattern} ({self.hit.masked})"
        return where + (f" in history {gitutil.short_sha(self.commit)}" if self.commit else "")


@dataclass
class ScanReport:
    findings: list[Finding] = field(default_factory=list)
    allowed: int = 0
    files: int = 0
    skipped_files: int = 0
    commits: int = 0


def scan_tree(ctx: Context, allow: Allowlist, report: ScanReport, seen: set[tuple[str, str]]) -> None:
    """Scan the working-tree copy of every tracked file."""
    for rel in ctx.tracked_files():
        path = ctx.project / rel
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = read_text(path)
        except OSError:
            report.skipped_files += 1
            continue
        if text is None:  # binary or over 1 MB
            report.skipped_files += 1
            continue
        report.files += 1
        for number, line in enumerate(text.splitlines(), start=1):
            for hit in find_secrets(line):
                seen.add((rel, hit.fingerprint))
                if allow.allows(rel, number, line):
                    report.allowed += 1
                else:
                    report.findings.append(Finding(rel, number, hit))


_COMMIT_MARK = "@@PREFLIGHT-COMMIT@@ "
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_history(
    project: Path, allow: Allowlist, report: ScanReport, seen: set[tuple[str, str]]
) -> None:
    """Scan every line ever added in any commit, oldest first (`git log -p --all`)."""
    lines = gitutil.stream(
        project,
        "log", "--all", "--reverse", "-p", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
        f"--format={_COMMIT_MARK}%H",
        timeout=HISTORY_TIMEOUT,
    )
    commit = ""
    path = ""
    new_line = 0
    in_hunk = False
    try:
        for raw in lines:
            if raw.startswith(_COMMIT_MARK):
                commit = raw[len(_COMMIT_MARK):].strip()
                report.commits += 1
                in_hunk = False
            elif raw.startswith("diff --git "):
                in_hunk = False
            elif in_hunk and raw.startswith("+"):
                text = raw[1:]
                for hit in find_secrets(text):
                    key = (path, hit.fingerprint)
                    if key in seen:
                        continue  # same value in the same file: already reported or accepted once
                    seen.add(key)
                    if allow.allows(path, new_line, text):
                        report.allowed += 1
                    else:
                        report.findings.append(Finding(path, new_line, hit, commit))
                new_line += 1
            elif in_hunk and raw.startswith(("-", "\\")):
                continue
            elif (match := _HUNK.match(raw)) is not None:
                in_hunk = True
                new_line = int(match.group(1))
            elif not in_hunk and raw.startswith("+++ "):
                name = raw[4:].split("\t")[0].strip().strip('"')
                path = "" if name == "/dev/null" else name.removeprefix("b/")
    finally:
        lines.close()


def secret_scan(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    allow = load_allowlist(ctx.project)
    report = ScanReport()
    seen: set[tuple[str, str]] = set()
    scan_tree(ctx, allow, report, seen)
    if p["history"]:
        scan_history(ctx.project, allow, report, seen)

    notes = []
    if report.allowed:
        notes.append(f"{report.allowed} finding(s) allowed via {ALLOW_FILE}")
    if allow.invalid:
        notes.append(f"{allow.invalid} invalid line(s) in {ALLOW_FILE} ignored")
    suffix = ("; " + "; ".join(notes)) if notes else ""

    if report.findings:
        labels = [f.label() for f in report.findings]
        return failed(f"{len(labels)} secret(s) found: {shorten(labels, SHOWN_FINDINGS)}{suffix}")
    scope = f"{report.files} tracked file(s)"
    if p["history"]:
        scope += f" and {report.commits} commit(s) of history"
    return passed(f"no secrets in {scope}{suffix}")


SECRET_SCAN = CheckType(
    "secret_scan",
    secret_scan,
    {"history": Param("bool", default=False)},
    "No credential in tracked files (AWS, GitHub, Slack, Discord, PEM, sk- keys, KEY/TOKEN/SECRET/PASSWORD "
    "assignments); with `history = true`, none in any commit either. Findings are masked. "
    f"`{ALLOW_FILE}` lists accepted findings.",
)
