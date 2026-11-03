"""command: run a shell command in the project and pass on exit code 0."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping
from typing import Any

from ..model import Outcome, failed, passed, skipped
from .base import CheckType, Context, Param, clip
from .secrets import redact


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """Kill the shell and everything it started, so no child keeps our pipes open."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def command(ctx: Context, p: Mapping[str, Any]) -> Outcome:
    timeout = float(p["timeout"])
    try:
        proc = subprocess.Popen(
            p["cmd"],
            shell=True,
            cwd=ctx.project,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(ctx.env),
            start_new_session=True,
        )
    except OSError as exc:
        return skipped(f"could not start the command: {exc}")
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.communicate()
        return failed(f"timed out after {timeout:g} s")
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    last = redact(clip(lines[-1], 80)) if lines else "no output"
    if proc.returncode == 0:
        return passed(f"exit 0: {last}")
    return failed(f"exit {proc.returncode}: {last}")


COMMAND = CheckType(
    "command",
    command,
    {"cmd": Param("str", required=True), "timeout": Param("number", default=120, minimum=1)},
    "Runs `cmd` in the project through the shell, with `timeout` seconds (default 120). PASS on exit 0. "
    "It runs code: a person must approve it.",
    runs_code=True,
)
