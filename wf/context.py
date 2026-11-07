"""What every command gets: the project folder, its settings, the state store, git, and shared checks."""

from __future__ import annotations

import select
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import machine
from .chat import short
from .config import Config
from .errors import Refused, WfError
from .gitops import Git
from .machine import short_sha
from .state import Store
from .timeutil import now

WF_FILES = (".workflow/", "wf", ".claude/settings.json.bak")


@dataclass
class Ctx:
    project: Path
    config: Config
    store: Store
    git: Git
    stdin: str | None = None

    # ---- names and state

    def alias(self, role: str) -> str:
        return self.config.alias(role)

    def load(self) -> dict:
        return self.store.load()

    @staticmethod
    def active(state: dict) -> dict | None:
        task_id = state.get("active")
        return state["tasks"].get(task_id) if task_id else None

    @staticmethod
    def last(state: dict) -> dict | None:
        """The active task, else the newest one, else None."""
        active = Ctx.active(state)
        if active is not None:
            return active
        tasks = list(state["tasks"].values())
        return max(tasks, key=lambda t: t["n"]) if tasks else None

    def find(self, state: dict, task_id: str | None) -> dict | None:
        if not task_id:
            return self.last(state)
        wanted = task_id.strip().upper()
        if not wanted.startswith("T"):
            wanted = "T" + wanted
        task = state["tasks"].get(wanted)
        if task is None:
            raise Refused(f"There is no task {task_id.strip()}.")
        return task

    def records(self, task: dict) -> str:
        return f"docs/tasks/{task['id']}"

    def task_dir(self, task_id: str) -> Path:
        return self.store.task_dir(task_id)

    # ---- input

    def read_input(self, file: str | None) -> str:
        """The text of --file, else what the test passed in, else stdin when it is not a terminal."""
        if file:
            path = Path(file).expanduser()
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise Refused(f"Cannot read {file}: {exc}") from None
        if self.stdin is not None:
            return self.stdin
        return read_stdin()

    # ---- the timeline and refusals

    def log(self, task: dict, command: str, result: str = "ok", **extra: object) -> None:
        event = {"at": now(), "command": command, "stage": task["stage"], "result": result}
        event.update({key: value for key, value in extra.items() if value is not None})
        self.store.append_timeline(task["id"], event)

    def refuse(self, task: dict | None, command: str, text: str) -> Refused:
        """The Refused error for TEXT, logged in the task's timeline (the state file is never touched)."""
        if task is not None:
            self.log(task, command, "refused", detail=short(text.splitlines()[0], 160))
        return Refused(text)

    def check(self, state: dict, command: str, *, head_moved: bool = False) -> dict | None:
        """The active task when the transition table allows COMMAND now; else raise the refusal."""
        task = self.active(state)
        reason = machine.refusal(task, command, head_moved=head_moved, alias=self.alias)
        if reason:
            raise self.refuse(task, command, reason)
        return task

    def slip(self, task: dict, command: str, what: str) -> None:
        self.log(task, command, "slip", detail=short(what, 200))

    # ---- the working tree

    def clean_entries(self, task: dict | None) -> list[str]:
        """Paths that make the tree not clean (spec section 8): wf's own files never count, and before
        `build start` the untracked records of TASK do not count either."""
        records = self.records(task) + "/" if task is not None and not task["build"]["started"] else None
        found: list[str] = []
        for code, path in self.git.status_entries():
            if code == "??":
                if path.startswith(".workflow/") or path in WF_FILES:
                    continue
                if records and path.startswith(records):
                    continue
            found.append(path)
        return found

    def require_clean(self, task: dict | None, command: str, what: str = "the tree is not clean") -> None:
        entries = self.clean_entries(task)
        if entries:
            listed = ", ".join(entries[:4]) + (f" and {len(entries) - 4} more" if len(entries) > 4 else "")
            if task is not None and task["build"]["started"]:
                fix = "Commit them with ./wf commit, or a person moves them away by hand."
            else:
                fix = "A person commits or moves them away by hand, outside the room."
            raise self.refuse(task, command, f"Not now: {what}: {listed}. {fix}")

    def ensure_on_branch(self, task: dict, command: str) -> None:
        """Put the project folder back on the task branch when a person moved it and the tree is clean."""
        branch = self.git.branch()
        if branch == task["branch"]:
            return
        if not self.git.branch_exists(task["branch"]):
            raise self.refuse(task, command, f"Not now: the branch {task['branch']} of {task['id']} is gone. A person must look at it.")
        entries = self.clean_entries(task)
        if entries:
            listed = ", ".join(entries[:4])
            raise self.refuse(task, command, f"Not now: the project folder is on {branch or 'a detached HEAD'}, not on {task['branch']}, "
                                             f"and has changes: {listed}. A person moves them away by hand.")
        self.git.switch(task["branch"])
        self.slip(task, command, f"the project folder was on {branch or 'a detached HEAD'}; wf switched it back to {task['branch']}")

    def branch_tip(self, task: dict) -> str | None:
        return self.git.rev(task["branch"])

    def require_head(self, task: dict, command: str) -> str:
        """The folder is on the task branch and HEAD is the commit `build done` recorded; returns HEAD."""
        self.ensure_on_branch(task, command)
        head = self.git.head()
        if head != task["head"]:
            raise self.refuse(task, command, f"HEAD moved since build done (was {short_sha(task['head'])}, now {short_sha(head)}). "
                                             f"The {self.alias('builder')} runs ./wf build done again.")
        return head

    def move_aside(self, source: Path, target_dir: Path, stem: str) -> Path:
        """Move SOURCE (a file or folder wf wrote) to TARGET_DIR/STEM, numbered when the name is taken."""
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix if source.is_file() else ""
        target = target_dir / f"{stem}{suffix}"
        number = 2
        while target.exists():
            target = target_dir / f"{stem}-{number}{suffix}"
            number += 1
        shutil.move(str(source), str(target))
        return target

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.project).as_posix()
        except ValueError:
            return str(path)


def read_stdin(wait: float = 2.0) -> str:
    """What was piped in (a heredoc, a file, /dev/null). A terminal, or a pipe that sends nothing for WAIT
    seconds, counts as empty: a forgotten heredoc must be a quick refusal, never a hang."""
    stream = sys.stdin
    try:
        if stream is None or stream.closed or stream.isatty():
            return ""
        ready, _, _ = select.select([stream.fileno()], [], [], wait)
    except (OSError, ValueError):
        return ""
    return stream.read() if ready else ""


def make_ctx(project: Path, stdin: str | None = None, config: Config | None = None) -> Ctx:
    from .config import load_config

    git = Git(project)
    if not project.is_dir():
        raise WfError(f"There is no folder {project}.")
    if not git.is_repo():
        raise WfError(f"{project} is not a git repository. wf works in a project folder that is a git repository.")
    return Ctx(project=project, config=config or load_config(project), store=Store(project / ".workflow"), git=git, stdin=stdin)
