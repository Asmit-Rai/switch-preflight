"""Helpers for the wf tests: a throwaway project folder, wf run in-process, a fixed clock.

Every test gets its own git repo with `main`, a `.workflow/project.toml` written directly (so the core
tests do not depend on `wf init`), the machine's git config ignored, and `WF_NOW` fixing the clock.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from helpers import ROOT, git, git_env, make_repo, write
from wf import chat, cli

CLOCK = "2026-11-05T10:00:00+05:30"

SPEC_4 = """Done means:
1. Ctrl+D with one shape selected adds a copy 10px right and down [unit]
2. Works with several shapes selected [unit]
3. One undo step [unit]
4. The copy is selected afterwards [browser]
Assumed: offset 10px; copies keep colour and fill.
"""

SPEC_2 = "1. The first file exists [unit]\n2. The second file exists [unit]\nAssumed: nothing\n"
PLAN_2 = (
    "1. first file | items 1 | unit | tests/1.sh | sh tests/1.sh\n"
    "2. second file | items 2 | unit | tests/2.sh | sh tests/2.sh\n"
)


class WfCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        env = git_env()
        env.update(WF_NOW=CLOCK, WF_TMPDIR=str(self.tmp / "clones"))
        env.pop("WF_SHIP", None)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.minutes = 0
        self.project = make_repo(self.tmp / "proj", {"README.md": "# proj\n"})
        self.configure()

    # ---- setup

    def configure(self, **settings: object) -> None:
        values: dict[str, object] = {
            "kit": str(ROOT), "main": "main", "install": "true", "suite": ["true"], "max_loops": 3, "run_timeout": 60,
        }
        values.update(settings)
        lines = [f"{key} = {json.dumps(value)}" for key, value in values.items() if not isinstance(value, dict)]
        for key, value in values.items():
            if isinstance(value, dict):
                lines.append(f"[{key}]")
                lines += [f"{k} = {json.dumps(v)}" for k, v in value.items()]
        write(self.project, ".workflow/project.toml", "\n".join(lines) + "\n")
        exclude = self.project / ".git" / "info" / "exclude"
        text = exclude.read_text() if exclude.exists() else ""
        if "/.workflow/" not in text:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(text + "\n/.workflow/\n/wf\n")

    # ---- running wf

    def run_wf(self, *argv: str, stdin: str | None = None) -> tuple[int, str]:
        return cli.run(list(argv), project=self.project, stdin=stdin)

    def wf(self, *argv: str, stdin: str | None = None, code: int = 0, speaker: str | None = None) -> str:
        got, text = self.run_wf(*argv, stdin=stdin)
        self.assertEqual(got, code, f"./wf {' '.join(argv)} gave exit {got}:\n{text}")
        if argv and argv[0] != "skill" and "--json" not in argv:
            self.assertEqual(chat.problems(text, speaker), [], f"house style of ./wf {' '.join(argv)}:\n{text}")
        return text

    def refused(self, *argv: str, stdin: str | None = None) -> str:
        return self.wf(*argv, stdin=stdin, code=2)

    # ---- reading what happened

    def state(self) -> dict:
        path = self.project / ".workflow" / "state.json"
        return json.loads(path.read_text()) if path.exists() else {"version": 1, "active": None, "tasks": {}}

    def task(self, task_id: str | None = None) -> dict:
        state = self.state()
        return state["tasks"][task_id or state["active"]]

    def raw_state(self) -> bytes:
        path = self.project / ".workflow" / "state.json"
        return path.read_bytes() if path.exists() else b""

    def git(self, *args: str) -> str:
        return git(self.project, *args)

    def tick(self, minutes: int) -> None:
        self.minutes += minutes
        when = datetime.fromisoformat(CLOCK) + timedelta(minutes=self.minutes)
        os.environ["WF_NOW"] = when.isoformat(timespec="seconds")

    # ---- moving a task along

    def start_task(self, goal: str = "add the two files", by: str = "asmit") -> str:
        return self.wf("new", goal, "--by", by, speaker="manager")

    def to_plan(self, spec: str = SPEC_2) -> None:
        self.start_task()
        self.wf("spec", "submit", stdin=spec, speaker="specifier")

    def to_build(self) -> None:
        self.to_plan()
        self.wf("plan", "submit", stdin=PLAN_2, speaker="planner")


class FlowCase(WfCase):
    """A WfCase that can walk a two-step task through build, review and test with real red and green runs.

    Step k's test is tests/k.sh (`test -f src/k.txt`), run by `sh tests/k.sh`; its code is src/k.txt.
    """

    def write_test(self, k: int) -> None:
        write(self.project, f"tests/{k}.sh", f"test -f src/{k}.txt\n")

    def write_code(self, k: int) -> None:
        write(self.project, f"src/{k}.txt", f"file {k}\n")

    def build_step(self, k: int) -> None:
        self.write_test(k)
        self.wf("run", "--step", str(k), "--red", speaker="builder")
        self.wf("commit", "--step", str(k), "--test", "-m", f"file {k} exists", "--", f"tests/{k}.sh", speaker="builder")
        self.write_code(k)
        self.wf("run", "--step", str(k), "--green", speaker="builder")
        self.wf("commit", "--step", str(k), "-m", f"add file {k}", "--", f"src/{k}.txt", speaker="builder")
        self.wf("run", "--suite", speaker="builder")

    def to_review(self) -> None:
        self.to_build()
        self.wf("build", "start", speaker="builder")
        self.build_step(1)
        self.build_step(2)
        self.wf("build", "done", speaker="builder")

    def to_test(self) -> None:
        self.to_review()
        self.wf("review", "approve", "--findings", "Tests check behaviour; code does what the items say.", speaker="reviewer")

    def report_items(self, results: dict[int, str] | None = None) -> None:
        for n in (1, 2):
            result = (results or {}).get(n, "PASS")
            self.wf("test", "item", str(n), result, "--evidence", f"sh tests/{n}.sh: exit {0 if result == 'PASS' else 1}", speaker="tester")

    def to_ship(self) -> None:
        self.to_test()
        self.wf("test", "start", speaker="tester")
        self.report_items()
        self.wf("test", "done", speaker="tester")


# Template-reading helpers (the template is read line by line; no YAML library).
def section(text: str, key: str) -> list[str]:
    """The lines of the top-level block `key:`, up to the next top-level key."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(key + ":"))
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"[A-Za-z_]+:", lines[i])), len(lines))
    return lines[start + 1 : end]


def listed(value: str) -> set[str]:
    """`[Write, Edit]` -> {"Write", "Edit"}."""
    return set(re.findall(r"[A-Za-z]+", value))


def parse_agents(text: str) -> dict[str, dict[str, Any]]:
    """{name: {"fields": {...}, "config": {...}}} for each `- name:` entry under `agents:`."""
    entries: list[list[str]] = []
    for line in section(text, "agents"):
        if line.startswith("  - name:"):
            entries.append([])
        if entries:
            entries[-1].append(line)
    agents: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = re.match(r'  - name: "?([^"\n]+)"?', entry[0]).group(1)  # type: ignore[union-attr]
        fields: dict[str, str] = {}
        config: dict[str, str] = {}
        i = 1
        while i < len(entry):
            m = re.match(r"    ([A-Za-z_]+):\s*(.*)$", entry[i])
            i += 1
            if not m:
                continue
            key, value = m.groups()
            if key == "instructions":
                body = []
                while i < len(entry) and (entry[i].startswith("      ") or not entry[i].strip()):
                    body.append(entry[i][6:])
                    i += 1
                fields[key] = "\n".join(body).strip()
            elif key == "advanced_config":
                while i < len(entry) and entry[i].startswith("      "):
                    k, v = entry[i].strip().split(":", 1)
                    config[k] = v.strip()
                    i += 1
            else:
                fields[key] = value.strip().strip('"')
        agents[name] = {"fields": fields, "config": config}
    return agents
