"""The grammar of the two texts agents submit: the "Done means" items and the plan steps.

Strict on purpose: a line in the wrong shape is refused with its number, never dropped quietly,
because a dropped item is an item nobody builds or tests.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .chat import short
from .errors import Refused

CHECK_WORDS = {"unit": "unit", "api": "API", "browser": "browser", "manual": "manual"}
MAX_ITEMS = 30
MAX_ITEM_TEXT = 300

_ITEM = re.compile(r"^(\d+)\.\s+(.*?)\s*\[([^\[\]]*)\]\s*$")
_NUMBERED = re.compile(r"^(\d+)[.)]")
_ASSUMED = re.compile(r"^assumed\s*:\s*(.*)$", re.IGNORECASE)
_STEP = re.compile(r"^(\d+)\.\s+(.*)$")
_HEADER = re.compile(r"^(?:done means|steps)(?:\s*\([^)]*\))?\s*:?\s*$", re.IGNORECASE)
_SHA_LINE = "\nSHA-256: "


@dataclass(frozen=True)
class Item:
    n: int
    text: str
    check: str


@dataclass(frozen=True)
class Step:
    k: int
    text: str
    items: tuple[int, ...]
    type: str
    file: str
    command: str

    @property
    def runs(self) -> bool:
        """True when the step has a command that wf runs (every type but manual)."""
        return self.type != "manual"


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """(line number, text) with indented continuation lines joined to the line above."""
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw[:1] in (" ", "\t") and raw.strip() and out and out[-1][1].strip():
            first, previous = out[-1]
            out[-1] = (first, previous.rstrip() + " " + raw.strip())
        else:
            out.append((number, raw))
    return out


def _ignored(line: str) -> bool:
    return not line or line.startswith("#") or bool(_HEADER.match(line))


def parse_check(raw: str) -> str | None:
    """`api + Browser` -> `API + browser`; `browser: notes` keeps the notes; None when not a check."""
    head, colon, note = raw.partition(":")
    words = [w.strip().lower() for w in head.split("+")]
    if not words or any(w not in CHECK_WORDS for w in words):
        return None
    canon = " + ".join(CHECK_WORDS[w] for w in words)
    return f"{canon}: {note.strip()}" if colon and note.strip() else canon


def _refuse(kind: str, problems: list[str]) -> Refused:
    command = "./wf spec submit" if kind == "Spec" else "./wf plan submit"
    shown = problems[:6]
    more = [f"... and {len(problems) - 6} more"] if len(problems) > 6 else []
    return Refused("\n".join([f"{kind} refused:", *shown, *more, f"Fix it and run {command} again."]))


def parse_spec(text: str) -> tuple[list[Item], str]:
    """The items and the Assumed line of a spec text. Refused with every problem it finds."""
    problems: list[str] = []
    items: list[Item] = []
    assumed: str | None = None
    for number, raw in _logical_lines(text):
        line = raw.strip()
        if _ignored(line):
            continue
        match = _ASSUMED.match(line)
        if match:
            if assumed is not None:
                problems.append(f"line {number}: a second Assumed line (put every assumption on one line)")
            elif not match.group(1).strip():
                problems.append(f"line {number}: the Assumed line is empty: write what you assumed, or nothing")
                assumed = ""
            else:
                assumed = match.group(1).strip()
            continue
        match = _ITEM.match(line)
        if match:
            n, body, raw_check = int(match.group(1)), match.group(2).strip(), match.group(3)
            check = parse_check(raw_check)
            if check is None:
                problems.append(f"line {number}: [{raw_check}] is not a check type: use unit, API, browser or manual, joined with +")
            if not body:
                problems.append(f"line {number}: item {n} has no text")
            if len(body) > MAX_ITEM_TEXT:
                problems.append(f"line {number}: item {n} is over {MAX_ITEM_TEXT} characters: split it")
            items.append(Item(n, body, check or raw_check))
            continue
        match = _NUMBERED.match(line)
        if match:
            problems.append(f"line {number}: item {match.group(1)} has no check type: end it with [unit], [API], [browser] or [manual]")
            continue
        problems.append(f"line {number}: not an item or the Assumed line: {short(line, 60)}")
    if not items and not problems:
        problems.append("no items: write at least one line like 1. <what is true when it is done> [unit]")
    numbers = [item.n for item in items]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"items must be numbered 1 to {len(numbers)} in order (found {', '.join(map(str, numbers))})")
    if len(items) > MAX_ITEMS:
        problems.append(f"more than {MAX_ITEMS} items: split the goal into smaller tasks")
    if assumed is None:
        problems.append("no Assumed line: add one, for example Assumed: nothing")
    if problems:
        raise _refuse("Spec", problems)
    return items, assumed or ""


def render_spec(task_id: str, goal: str, items: list[Item], assumed: str) -> tuple[str, str]:
    """The text of docs/tasks/<id>/spec.md and the SHA-256 of everything above its last line."""
    lines = [f"# {task_id} spec: {short(goal, 120)}", "", "Done means:"]
    lines += [f"{item.n}. {item.text} [{item.check}]" for item in items]
    lines += ["", f"Assumed: {assumed}", ""]
    body = "\n".join(lines)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"{body}{_SHA_LINE}{sha}\n", sha


def spec_sha(text: str) -> str | None:
    """The SHA-256 a spec.md file claims, or None when the file was changed after it was written."""
    body, sep, tail = text.rpartition(_SHA_LINE)
    claimed = tail.strip()
    if not sep or hashlib.sha256(body.encode("utf-8")).hexdigest() != claimed:
        return None
    return claimed


def parse_items(field: str) -> list[int] | None:
    """`items 1, 3` or `item 2` or `1-3` -> sorted numbers; None when unreadable."""
    rest = re.sub(r"^items?\b", "", field.strip(), flags=re.IGNORECASE).strip()
    tokens = [t for t in re.split(r"[,\s]+", rest) if t]
    if not tokens:
        return None
    numbers: set[int] = set()
    for token in tokens:
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            return None
        low = int(match.group(1))
        high = int(match.group(2) or low)
        if high < low or high - low > MAX_ITEMS:
            return None
        numbers.update(range(low, high + 1))
    return sorted(numbers)


def parse_plan(text: str, item_numbers: list[int]) -> list[Step]:
    """The steps of a plan text, checked against the spec's item numbers."""
    problems: list[str] = []
    steps: list[Step] = []
    for number, raw in _logical_lines(text):
        line = raw.strip()
        if _ignored(line):
            continue
        match = _STEP.match(line)
        if not match:
            problems.append(f"line {number}: not a step: {short(line, 60)}")
            continue
        k = int(match.group(1))
        parts = [part.strip() for part in match.group(2).split("|", 4)]
        if len(parts) != 5:
            problems.append(f"line {number}: step {k} needs 5 parts: what | items | type | test file | command")
            continue
        what, items_field, type_field, file, command = parts
        kind = CHECK_WORDS.get(type_field.lower())
        if kind is None:
            problems.append(f"line {number}: step {k} type {type_field or '(empty)'}: use unit, API, browser or manual")
        items = parse_items(items_field)
        if items is None:
            problems.append(f"line {number}: step {k}: items must be numbers like items 1, 3 or items 1-2")
            items = []
        for n in items:
            if n not in item_numbers:
                problems.append(f"line {number}: step {k} serves item {n}, which is not in the spec")
        if not what:
            problems.append(f"line {number}: step {k} does not say what it adds")
        if kind == "manual":
            if not command:
                problems.append(f"line {number}: manual step {k}: write the words to follow in the last part")
        else:
            if file in ("", "-"):
                problems.append(f"line {number}: step {k} has no test file")
            if not command:
                problems.append(f"line {number}: step {k} has no command")
        steps.append(Step(k, what, tuple(items), kind or type_field, file or "-", command))
    if not steps and not problems:
        problems.append("no steps: write lines like 1. <what> | items 1 | unit | <test file> | <command>")
    numbers = [step.k for step in steps]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"steps must be numbered 1 to {len(numbers)} in order (found {', '.join(map(str, numbers))})")
    if len(steps) > MAX_ITEMS:
        problems.append(f"more than {MAX_ITEMS} steps: split the task")
    served = {n for step in steps for n in step.items}
    for n in item_numbers:
        if n not in served:
            problems.append(f"item {n} has no step")
    if problems:
        raise _refuse("Plan", problems)
    return steps


def render_plan(task_id: str, goal: str, steps: list[Step]) -> str:
    """The text of docs/tasks/<id>/plan.md (it parses back to the same steps)."""
    lines = [f"# {task_id} plan: {short(goal, 120)}", "", "Steps (what | items | type | test file | command):"]
    for step in steps:
        items = ", ".join(map(str, step.items))
        lines.append(f"{step.k}. {step.text} | items {items} | {step.type} | {step.file} | {step.command}")
    return "\n".join(lines) + "\n"


def item_from(record: dict) -> Item:
    return Item(int(record["n"]), record["text"], record["check"])


def step_from(record: dict) -> Step:
    return Step(int(record["k"]), record["text"], tuple(record["items"]), record["type"], record["file"], record["command"])


def step_record(step: Step) -> dict:
    return {"k": step.k, "text": step.text, "items": list(step.items), "type": step.type, "file": step.file, "command": step.command}


def item_record(item: Item) -> dict:
    return {"n": item.n, "text": item.text, "check": item.check}
