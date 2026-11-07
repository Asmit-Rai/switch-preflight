"""The git calls wf makes, in one place. Every failure becomes a WfError with git's own last line."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import WfError

ZERO = "0" * 40


class GitError(WfError):
    """git is missing, the folder is not a repository, or a git command failed."""


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    subject: str


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


class Git:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def run(self, *args: str, check: bool = True, env: dict[str, str] | None = None, timeout: float = 300,
            stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        full_env = dict(os.environ)
        full_env["GIT_TERMINAL_PROMPT"] = "0"
        if env:
            full_env.update(env)
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.root), "-c", "core.quotepath=false", *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=full_env, timeout=timeout, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise GitError("git is not installed") from None
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} took longer than {timeout:.0f}s") from None
        if check and proc.returncode != 0:
            reason = _last_line(proc.stderr) or _last_line(proc.stdout) or f"exit {proc.returncode}"
            raise GitError(f"git {args[0]} failed: {reason}")
        return proc

    def out(self, *args: str, **kwargs) -> str:
        return self.run(*args, **kwargs).stdout.strip()

    def ok(self, *args: str) -> bool:
        return self.run(*args, check=False).returncode == 0

    # ---- reading

    def is_repo(self) -> bool:
        return self.ok("rev-parse", "--git-dir")

    def head(self) -> str:
        return self.out("rev-parse", "HEAD")

    def branch(self) -> str:
        """The checked-out branch, or "" when HEAD is detached."""
        proc = self.run("symbolic-ref", "--short", "-q", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def rev(self, ref: str) -> str | None:
        proc = self.run("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def branch_exists(self, name: str) -> bool:
        return self.ok("show-ref", "--verify", "-q", f"refs/heads/{name}")

    def refs(self) -> list[str]:
        """Short names of local branches and remote-tracking branches."""
        text = self.out("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
        return [line for line in text.splitlines() if line]

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", older, newer)

    def remote_url(self, name: str = "origin") -> str | None:
        proc = self.run("remote", "get-url", name, check=False)
        return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None

    def tracked(self, path: str) -> bool:
        return bool(self.out("ls-files", "--", path))

    def status_entries(self) -> list[tuple[str, str]]:
        """(two-letter status, path) for every change, untracked files listed one by one."""
        proc = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        parts = proc.stdout.split("\0")
        entries: list[tuple[str, str]] = []
        i = 0
        while i < len(parts):
            item = parts[i]
            i += 1
            if len(item) < 4:
                continue
            code, path = item[:2], item[3:]
            entries.append((code, path))
            if code[0] in "RC":  # a rename or copy is followed by its old path
                i += 1
        return entries

    def changed_paths(self) -> list[str]:
        """Every path git status names, rename and copy sources included."""
        proc = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        parts = proc.stdout.split("\0")
        found: list[str] = []
        i = 0
        while i < len(parts):
            item = parts[i]
            i += 1
            if len(item) < 4:
                continue
            found.append(item[3:])
            if item[0] in "RC" and i < len(parts) and parts[i]:
                found.append(parts[i])
                i += 1
        return found

    def commits(self, base: str, head: str = "HEAD") -> list[Commit]:
        """Commits in base..head, oldest first."""
        text = self.run("log", "--reverse", "--format=%H%x00%P%x00%s%x1e", f"{base}..{head}").stdout
        found: list[Commit] = []
        for record in text.split("\x1e"):
            record = record.strip("\n")
            if not record:
                continue
            sha, parents, subject = (record.split("\0") + ["", ""])[:3]
            found.append(Commit(sha, tuple(parents.split()), subject))
        return found

    def show(self, rev: str, path: str) -> str | None:
        proc = self.run("show", f"{rev}:{path}", check=False)
        return proc.stdout if proc.returncode == 0 else None

    # ---- changing

    def switch(self, branch: str, create_from: str | None = None) -> None:
        if create_from:
            self.run("switch", "-q", "-c", branch, create_from)
        else:
            self.run("switch", "-q", branch)
