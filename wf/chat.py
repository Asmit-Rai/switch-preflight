"""The house style of every chat message wf prints (spec section 11).

Under 1500 characters, no table, no JSON, no line over 180 characters, PASS and FAIL as words,
numbers as "3 of 4", never the name of a chat app, never the printing agent's own alias.
`problems()` is what the tests hold every output to; `fit()` is the safety net in production.
"""

from __future__ import annotations

import json
import re
import textwrap

MAX_CHARS = 1500
MAX_LINE = 180
CUT = "... (cut to fit: the files and ./wf status hold the rest)"

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_CHAT_APPS = re.compile(r"\b(?:Discord|Slack|Teams)\b")
_SLASH_COUNT = re.compile(r"(?<![\w./-])\d+/\d+(?![\w./-])")
_MARKS = "✓✔✗✘❌✅"


def problems(text: str, speaker: str | None = None) -> list[str]:
    """Every way TEXT breaks the house style (empty when it is fine)."""
    found: list[str] = []
    if len(text) > MAX_CHARS:
        found.append(f"over {MAX_CHARS} characters ({len(text)})")
    for number, line in enumerate(text.splitlines(), start=1):
        if len(line) > MAX_LINE:
            found.append(f"line {number} is over {MAX_LINE} characters")
        if _TABLE_ROW.match(line):
            found.append(f"line {number} is a table row")
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            json.loads(stripped)
            found.append("the message is JSON")
        except ValueError:
            pass
    if _CHAT_APPS.search(text):
        found.append("names a chat app")
    if _SLASH_COUNT.search(text):
        found.append('a count written like 3/4: write "3 of 4"')
    if any(mark in text for mark in _MARKS):
        found.append("a check mark: write PASS or FAIL")
    if speaker and re.search(rf"@{re.escape(speaker)}(?![\w-])", text):
        found.append(f"names its own alias @{speaker}")
    return found


def fit(text: str) -> str:
    """TEXT cut to the limits: long lines end in "...", a long message keeps its first and last line."""
    lines = [line if len(line) <= MAX_LINE else line[: MAX_LINE - 3] + "..." for line in text.splitlines()]
    joined = "\n".join(lines)
    if len(joined) <= MAX_CHARS or len(lines) < 3:
        return joined[:MAX_CHARS]
    first, last = lines[0], lines[-1]
    budget = MAX_CHARS - len(first) - len(last) - len(CUT) - 3
    middle: list[str] = []
    for line in lines[1:-1]:
        if len(line) + 1 > budget:
            break
        middle.append(line)
        budget -= len(line) + 1
    return "\n".join([first, *middle, CUT, last])


def bounded(lines: list[str], budget: int, where: str) -> list[str]:
    """As many of LINES as fit in BUDGET characters, then "... and N more in WHERE"."""
    out: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        if used + len(line) + 1 > budget:
            out.append(f"... and {len(lines) - index} more in {where}")
            return out
        out.append(line)
        used += len(line) + 1
    return out


def count(n: int, word: str) -> str:
    """`1 item`, `3 items`: a number with its word in the right form."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def numbered(word: str, numbers: list) -> str:
    """`step 1`, `steps 1, 2`."""
    return f"{word} {numbers[0]}" if len(numbers) == 1 else f"{word}s {', '.join(map(str, numbers))}"


def wrapped(text: str, width: int = MAX_LINE - 2) -> str:
    """TEXT on lines of at most WIDTH characters, broken at spaces. fit() cuts a longer line in the middle of a word and
    drops its end, and the end of a WAITING ON YOU or Assumed line is what tells the person what to do."""
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False))


def short(text: str, limit: int = 80) -> str:
    """One line of TEXT, at most LIMIT characters."""
    one = " ".join(str(text).split())
    return one if len(one) <= limit else one[: limit - 3].rstrip() + "..."
