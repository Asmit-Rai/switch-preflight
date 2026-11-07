#!/usr/bin/env python3
"""The git hooks of a wf project: pre-commit, pre-merge-commit and pre-push.

`wf init` installs one small shim per hook into the project's .git/hooks/, for example:

    #!/bin/sh
    # wf git hook: written by wf init. The rules live in the kit.
    exec "/abs/python3" "/abs/kit/wf/githooks.py" pre-commit "$@"

pre-commit refuses
  * any commit on the main branch (`main`, or the `main` key of .workflow/project.toml), unless
    WF_SHIP=1 is set: `./wf ship` sets it for its own merge (when there is no remote);
  * a staged node_modules folder (at any depth), build output (dist, build, out, coverage,
    test-results or playwright-report at the top of the repository) or .workflow/ (wf's own state).
pre-merge-commit applies the same rules to the commit `git merge` makes: git merge never runs
pre-commit. pre-push refuses unless .workflow/ship-token names the commit being pushed (`./wf ship`
writes the token just before it pushes and deletes it after), and it never lets a push delete a
remote branch.

Limits (stated plainly): `--no-verify` skips git hooks. A fast-forward merge makes no commit, and
cherry-pick, revert and am do not run pre-commit, so these hooks do not see them (the approval hook,
hooks/risky_git.py --policy wf, refuses them in Claude Code). pre-merge-commit needs git 2.24 or
later. On GitHub, protect main as well (pull request required, no force push).

It is run as a plain script by path, so it imports nothing from the wf package. Standard library
only. An unknown hook name is let through: a typo in a shim must never block every commit.
"""

from __future__ import annotations

import os
import sys

# Run by path, Python puts this file's folder (the wf package) first on sys.path, where a module named
# like a standard one (wf/types.py, say) would be imported in its place and break every commit. Nothing
# here comes from that folder, so it comes off the path before anything else is imported.
if __name__ == "__main__":
    _HERE = os.path.dirname(os.path.realpath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.realpath(p or os.curdir) != _HERE]

import subprocess  # noqa: E402
import tomllib  # noqa: E402
from collections.abc import Mapping  # noqa: E402
from pathlib import Path  # noqa: E402

BUILD_DIRS = ("dist", "build", "out", "coverage", "test-results", "playwright-report")


def main_branch(repo: Path) -> str:
    """The `main` key of .workflow/project.toml; "main" when the file or the key is missing or unreadable."""
    try:
        with open(repo / ".workflow" / "project.toml", "rb") as handle:
            value = tomllib.load(handle).get("main")
    except (OSError, ValueError):  # ValueError: not TOML (tomllib.TOMLDecodeError) or not UTF-8
        return "main"
    return value.strip() if isinstance(value, str) and value.strip() else "main"


def pre_commit(repo: Path, env: Mapping[str, str]) -> str | None:
    """None when this commit may go ahead, else why not. ENV is read for WF_SHIP only."""
    main = main_branch(repo)
    branch = _git(repo, "symbolic-ref", "--short", "-q", "HEAD").strip()  # "" when HEAD is detached
    if branch == main and env.get("WF_SHIP") != "1":
        return f"wf: no commits on {main}. Work happens on the task branch (./wf build start); only ./wf ship merges into {main}."
    # Added, copied, modified or renamed (to the new name). Deleting tracked build output is allowed.
    staged = _git(repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    for path in staged.split("\0"):
        parts = path.split("/")
        if path and ("node_modules" in parts or parts[0] in BUILD_DIRS or parts[0] == ".workflow"):
            return f"wf: refusing to commit {path}: node_modules, build output and .workflow/ never go into git."
    return None


def pre_push(repo: Path, lines: list[str]) -> str | None:
    """None when this push may go ahead, else why not.

    LINES are what git writes to the hook's stdin, one per ref: <local ref> <local sha> <remote ref>
    <remote sha>. A local sha of zeros is a delete. A remote sha of zeros is only a new branch.
    """
    token = _ship_token(repo)
    if not token:
        return "wf: push refused: only ./wf ship pushes (there is no ship token)."
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        sha = fields[1] if len(fields) > 1 else ""
        if sha and set(sha) == {"0"}:  # 40 zeros, or 64 in a SHA-256 repository
            return "wf: push refused: deleting a remote branch is not allowed."
        if sha != token:
            return f"wf: push refused: the ship token names {token[:7]}, not {sha[:7]}."
    return None


def main(argv: list[str] | None = None) -> int:
    """Run the hook named by the first argument (the shim passes git's own arguments after it)."""
    args = sys.argv[1:] if argv is None else argv
    hook = args[0] if args else ""
    if hook not in ("pre-commit", "pre-merge-commit", "pre-push"):
        return 0  # an unknown hook name never blocks work
    repo = _repo()
    if hook == "pre-push":
        refusal = pre_push(repo, sys.stdin.read().splitlines())
    else:  # pre-commit, and pre-merge-commit: the same rules for the commit git merge makes
        refusal = pre_commit(repo, os.environ)
    if refusal is None:
        return 0
    print(refusal, file=sys.stderr)
    return 1


def _ship_token(repo: Path) -> str:
    """The stripped content of .workflow/ship-token; "" when there is none or it cannot be read."""
    try:
        return (repo / ".workflow" / "ship-token").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def _repo() -> Path:
    """The top of the working tree, where git runs hooks; the current folder when git cannot say."""
    top = _git(Path.cwd(), "rev-parse", "--show-toplevel").strip()
    return Path(top) if top else Path.cwd()


def _git(repo: Path, *args: str) -> str:
    """The output of `git -C REPO ARGS`; "" when git cannot run.

    The environment is the hook's own (for a commit hook git sets GIT_INDEX_FILE to the index being
    committed, which `git commit -a` or `git commit <paths>` builds apart from .git/index), with
    GIT_OPTIONAL_LOCKS=0 so these reads never take a lock.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            check=False,
        )
    except OSError:
        return ""
    return done.stdout


if __name__ == "__main__":
    raise SystemExit(main())
