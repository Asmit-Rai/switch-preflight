"""Running a command and keeping what it printed: the evidence a step, a fix or the suite leaves behind.

A command runs in its own process group with stdin closed, so a test runner never waits for a key and a
server it starts in the background is stopped with it at the timeout. The whole output goes to a file;
only the lines that decide the result go into the chat (never the timing lines).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .timeutil import now

TIMED_OUT = 124
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_DECIDING = [
    re.compile(r"^\s*(?:Test Files|Tests|Test Suites)\s+\d"),  # vitest, jest
    re.compile(r"^\s*\d+ (?:passed|failed|flaky|skipped)\b"),  # playwright
    re.compile(r"=+ .*\b(?:passed|failed|errors?)\b.* =+"),  # pytest
    re.compile(r"^(?:FAILED|ERROR) \S"),  # pytest
    re.compile(r"^Ran \d+ tests? in"),  # unittest
    re.compile(r"^(?:OK|FAILED)\b"),  # unittest
    re.compile(r"✖ \d+ problems?"),  # eslint
    re.compile(r"\berror TS\d+"),  # tsc
    re.compile(r"(?:^|\s)FAIL\s"),  # vitest, jest
]
_ERRORISH = re.compile(r"\b(?:Error|ERR!|AssertionError|Traceback|not ok|No such file|not found|cannot)\b", re.IGNORECASE)
_MARKS = {"✓": "PASS", "✔": "PASS", "✅": "PASS", "✗": "FAIL", "✘": "FAIL", "×": "FAIL", "❌": "FAIL"}


@dataclass(frozen=True)
class RunResult:
    command: str
    exit: int
    output: str
    seconds: float
    timed_out: bool
    started: str


def execute(command: str, cwd: Path, timeout: float, env: dict[str, str] | None = None) -> RunResult:
    """Run COMMAND with the shell in CWD. A command still running after TIMEOUT seconds is stopped."""
    started = now()
    begin = time.monotonic()
    proc = subprocess.Popen(
        command, shell=True, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", start_new_session=True, env=env,
    )
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout)
    except BaseException as exc:
        if not isinstance(exc, subprocess.TimeoutExpired):
            # wf itself is being stopped (the agent's command timed out, or a person pressed Ctrl+C):
            # never leave the step's processes behind holding a port.
            _kill_group(proc, signal.SIGKILL)
            proc.wait()
            raise

        timed_out = True
        _kill_group(proc, signal.SIGTERM)
        try:
            output, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_group(proc, signal.SIGKILL)
            output, _ = proc.communicate()
    code = TIMED_OUT if timed_out else proc.returncode
    return RunResult(command, code, output or "", time.monotonic() - begin, timed_out, started)


def _kill_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def header(result: RunResult, cwd: Path, head: str) -> str:
    stopped = " (timed out: stopped)" if result.timed_out else ""
    return "\n".join([
        f"command: {result.command}",
        f"cwd: {cwd}",
        f"head: {head}",
        f"started: {result.started}",
        f"seconds: {result.seconds:.1f}",
        f"exit: {result.exit}{stopped}",
        "",
        "",
    ])


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def attempt_path(folder: Path, name: str) -> Path:
    """`<name>-attempt-<i>.txt`, the first number not taken."""
    number = 1
    while (folder / f"{name}-attempt-{number}.txt").exists():
        number += 1
    return folder / f"{name}-attempt-{number}.txt"


def digest(output: str, limit: int = 8, failed: bool = False, decisive_only: bool = False) -> list[str]:
    """The lines that decide a result: test counts, FAIL lines, error lines. Check marks become words.

    When the run FAILED, the first error line (why it failed) always comes first: the reviewer judges
    "fails for the right reason" from it.
    """
    text = _ANSI.sub("", output)
    for mark, word in _MARKS.items():
        text = text.replace(mark, word)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    errors = [line for line in lines if _ERRORISH.search(line) and not line.lstrip().startswith(("\u276f", "at ", "File:"))]
    picked = [line for line in lines if any(p.search(line) for p in _DECIDING)]
    if failed and errors and errors[0] not in picked:
        picked.insert(0, errors[0])
    if not picked and not decisive_only:
        first_error = errors[0] if errors else None
        picked = ([first_error] if first_error else []) + [line for line in lines[-3:] if line != first_error]
    out: list[str] = []
    for line in picked:
        line = line.strip()
        line = line if len(line) <= 160 else line[:157] + "..."
        if line not in out:
            out.append(line)
        if len(out) >= limit:
            break
    return out
