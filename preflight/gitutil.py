"""Small read-only wrappers around the git command line.

Every call sets GIT_OPTIONAL_LOCKS=0 so that reading never writes into .git.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

IGNORED_DIR = ".preflight"  # Preflight's own output: never counts as a change


class GitError(Exception):
    """git is missing, the folder is not a repository, or a git command failed."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _argv(project: Path | str, args: tuple[str, ...]) -> list[str]:
    return ["git", "-C", str(project), "-c", "core.quotepath=false", *args]


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def run(project: Path | str, *args: str, timeout: float = 60.0) -> str:
    """Run `git -C project ARGS` and return stdout. Raise GitError on any failure."""
    try:
        proc = subprocess.run(
            _argv(project, args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env(),
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        reason = _last_line(proc.stderr) or f"exit {proc.returncode}"
        raise GitError(f"git {args[0]} failed: {reason}")
    return proc.stdout


def stream(project: Path | str, *args: str, timeout: float = 120.0) -> Iterator[str]:
    """Yield stdout lines of a git command one at a time (for very large output)."""
    stderr = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            _argv(project, args),
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env(),
        )
    except FileNotFoundError as exc:
        stderr.close()
        raise GitError("git is not installed") from exc
    deadline = time.monotonic() + timeout
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.monotonic() > deadline:
                raise GitError(f"git {args[0]} timed out after {timeout:.0f}s")
            yield line.rstrip("\n")
        if proc.wait() != 0:
            stderr.seek(0)
            reason = _last_line(stderr.read().decode("utf-8", errors="replace"))
            raise GitError(f"git {args[0]} failed: {reason or 'non-zero exit'}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        stderr.close()


def is_repo(project: Path | str) -> bool:
    try:
        return run(project, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


def head_sha(project: Path | str) -> str | None:
    """Full sha of HEAD, or None when there is no commit yet (or no repository)."""
    try:
        return run(project, "rev-parse", "--verify", "-q", "HEAD").strip() or None
    except GitError:
        return None


def short_sha(sha: str | None) -> str:
    return sha[:7] if sha else "none"


def tracked_files(project: Path | str) -> list[str]:
    """Paths (relative, `/`-separated) of every git-tracked file, sorted."""
    out = run(project, "ls-files", "-z")
    return sorted({path for path in out.split("\0") if path})


def project_files(project: Path | str) -> list[str]:
    """Tracked files plus untracked-but-not-ignored files that exist on disk."""
    out = run(project, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    root = Path(project)
    return sorted({p for p in out.split("\0") if p and os.path.lexists(root / p)})


def dirty_entries(project: Path | str) -> list[str]:
    """Uncommitted changes as short strings like 'M README.md' or '?? notes.txt'.

    The `.preflight/` folder (Preflight's own output) is never counted.
    """
    out = run(
        project,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
        f":(exclude){IGNORED_DIR}",
    )
    fields = out.split("\0")
    entries: list[str] = []
    i = 0
    while i < len(fields):
        item = fields[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        if "R" in xy or "C" in xy:
            i += 1  # a rename/copy is followed by one extra field: the old path
        entries.append(f"{xy.strip() or '?'} {path}")
    return entries


def origin_url(project: Path | str) -> str | None:
    try:
        return run(project, "remote", "get-url", "origin").strip() or None
    except GitError:
        return None


def on_remote_branch(project: Path | str, sha: str) -> bool:
    """Is the commit on a remote-tracking branch (`git branch -r --contains`)? Local refs only, no network."""
    if not 7 <= len(sha) <= 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
        return False  # only a hex sha reaches the command line
    try:
        return bool(run(project, "branch", "-r", "--contains", sha).strip())
    except GitError:
        return False


def commit_times(project: Path | str) -> list[tuple[str, datetime]]:
    """(sha, author date in UTC) for every commit reachable from any ref."""
    out = run(project, "log", "--all", "--format=%H %at")
    commits: list[tuple[str, datetime]] = []
    for line in out.splitlines():
        sha, _, stamp = line.partition(" ")
        if sha and stamp.strip().lstrip("-").isdigit():
            commits.append((sha, datetime.fromtimestamp(int(stamp), tz=timezone.utc)))
    return commits
