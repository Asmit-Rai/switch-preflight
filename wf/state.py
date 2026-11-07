"""The state file: every task's record, versioned, locked and written atomically (spec section 6.3).

Readers never need the lock: a write is `state.json.tmp` + rename, so a reader sees the old file or
the new one, never half of either. Writers hold an `fcntl` lock on `.workflow/lock` for the short
time between reloading the state and renaming the new file into place.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import WfError

STATE_VERSION = 1


def empty_state() -> dict:
    return {"version": STATE_VERSION, "active": None, "tasks": {}}


def new_task(n: int, goal: str, by: str, branch: str, base: str, at: str) -> dict:
    """A fresh task record at stage spec (the fields of spec section 6.1)."""
    return {
        "id": f"T{n}",
        "n": n,
        "goal": goal,
        "by": by,
        "created_at": at,
        "mode": "auto",
        "stage": "spec",
        "waiting": None,
        "stopped": False,
        "stopped_by": None,
        "stopped_at": None,
        "branch": branch,
        "base": base,
        "head": None,
        "spec": None,
        "plan": None,
        "build": {"started": False, "started_at": None, "records_commit": None, "evidence": {}, "fixes": {}, "suite": None},
        "fix_round": 0,
        "fix_reason": None,
        "review": None,
        "tests": empty_tests(),
        "loops": 0,
        "handoff": None,
        "last_activity": None,
        "quotes": [],
        "ship": None,
        "cancel_stash": None,
        "ended_at": None,
    }


def empty_tests() -> dict:
    return {"clone": None, "install": None, "head": None, "items": {}, "result": None, "report": None}


class Store:
    def __init__(self, workflow: Path) -> None:
        self.dir = Path(workflow)
        self.path = self.dir / "state.json"
        self.lock_path = self.dir / "lock"

    def load(self) -> dict:
        if not self.path.exists():
            return empty_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WfError(f".workflow/state.json is damaged ({exc}). Nothing was changed. Look at the file by hand.") from None
        if not isinstance(data, dict) or not isinstance(data.get("version"), int) or not isinstance(data.get("tasks", {}), dict):
            raise WfError(".workflow/state.json is damaged (no version or no tasks). Nothing was changed. Look at the file by hand.")
        if data["version"] > STATE_VERSION:
            raise WfError(f".workflow/state.json was written by a newer wf (version {data['version']}): update the kit.")
        data.setdefault("active", None)
        data.setdefault("tasks", {})
        return data

    @contextmanager
    def transaction(self, timeout: float = 30.0) -> Iterator[dict]:
        """Lock, reload, yield the state; save it if it changed and the block ended without an error."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise WfError("Another ./wf command is writing the state right now. Try again in a moment.") from None
                    time.sleep(0.05)
            try:
                state = self.load()
                before = json.dumps(state, sort_keys=True)
                yield state
                if json.dumps(state, sort_keys=True) != before:
                    self._save(state)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _save(self, state: dict) -> None:
        tmp = self.dir / "state.json.tmp"
        data = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def task_dir(self, task_id: str) -> Path:
        return self.dir / "tasks" / task_id

    def append_timeline(self, task_id: str, event: dict) -> None:
        folder = self.task_dir(task_id)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "timeline.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")

    def read_timeline(self, task_id: str) -> list[dict]:
        path = self.task_dir(task_id) / "timeline.jsonl"
        if not path.is_file():
            return []
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events
