"""`preflight target`: choose the project under test.

    kit/pf target SOURCE [--into DIR] [--branch NAME]
    kit/pf target --show [--into DIR]

SOURCE is `sample` (a fresh sample with 6 planted defects, made by the checker's own
examples/make_sample.py), an https or ssh git URL, or the path of a local git repository (write
`./sample` for a folder that is really called sample). DIR defaults to `project` in the current folder.

Loading is safe to repeat. The new project is cloned into a temporary folder next to DIR first; only
when that worked is the old DIR moved to `.old-projects/` next to it (nothing is ever deleted) and the
clone renamed to DIR. A failed clone leaves the old project as it was. What was loaded is recorded in
DIR/.preflight/target.json, never with credentials. The clone of a local repository takes that
repository's origin URL and its view of the remote (origin/*), so checks see the real GitHub repository
and an unpushed commit does not look pushed.

Refused sources: anything that starts with `-`, holds spaces, control characters or `::`, uses a scheme
other than https or ssh (file://, http://, git://, ext::), or carries credentials in the URL. git runs
with no terminal and GIT_TERMINAL_PROMPT=0, so a private repository fails at once instead of waiting
for a password. The clone is full (the history checks need every commit), without submodules.

Exit codes: 0 ok, 1 nothing loaded (--show only), 2 error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import gitutil
from .checks.gitchecks import parse_github_remote, redact_url
from .timeutil import now_iso
from .verdict import write_json

ROOT = Path(__file__).resolve().parent.parent  # the checker's own folder (kit/ in the shared folder)
MAKE_SAMPLE = ROOT / "examples" / "make_sample.py"
DEFAULT_INTO = "project"
OLD_PROJECTS = ".old-projects"
TARGET_FILE = "target.json"
CLONE_TIMEOUT = 600.0  # seconds
SAMPLE_TIMEOUT = 120.0
SOURCE_HELP = "an https or ssh git URL, the path of a local git repository, or sample"
LOGIN_HINT = "sign in with gh auth login or grant the agent a GitHub connection"
CREDENTIALS = (
    "the URL carries credentials (user:password@ or token@); Preflight never stores them: "
    "remove them, and for a private repository sign in with gh auth login"
)

_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"  # also an ssh config alias such as github_work
_PART = r"[A-Za-z0-9._~+-]+"
_HTTPS_URL = re.compile(rf"https://{_HOST}(?::\d{{1,5}})?(?:/{_PART})+/?")
_SSH_URL = re.compile(rf"ssh://(?:[A-Za-z0-9._-]+@)?{_HOST}(?::\d{{1,5}})?(?:/{_PART})+/?")
_SCP_URL = re.compile(rf"(?:[A-Za-z0-9._-]+@)?{_HOST}:[A-Za-z0-9._~+/][A-Za-z0-9._~+/-]*")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")


class TargetError(Exception):
    """A source, branch or folder that cannot be loaded. The message is one plain line."""


@dataclass(frozen=True)
class Source:
    """A checked SOURCE. `value` is the URL, the absolute path of the repository, or `sample`."""

    kind: str  # "url", "path" or "sample"
    value: str


def parse_source(text: str) -> Source:
    """Say what SOURCE is, or raise TargetError when it is refused."""
    if text == "sample":
        return Source("sample", "sample")
    if not text:
        raise TargetError(f"SOURCE is empty; give {SOURCE_HELP}")
    problem = _unsafe(text)
    if problem:
        raise TargetError(problem)
    scheme, sep, rest = text.partition("://")
    if sep:
        return Source("url", _check_url(text, scheme, rest))
    if _looks_like_scp(text):
        if "@" in text and ":" in text.split("@", 1)[0]:
            raise TargetError(CREDENTIALS)
        if not _SCP_URL.fullmatch(text):
            raise TargetError("not a valid ssh git URL (expected git@host:owner/repo.git)")
        return Source("url", text)
    return Source("path", _check_path(text))


def _unsafe(text: str) -> str | None:
    """Why this text must never reach git as a source or a remote URL, or None."""
    if text.startswith("-"):
        return "SOURCE must not start with '-'"
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in text):
        return "SOURCE must not contain spaces or control characters"
    if "::" in text:
        return "SOURCE must not contain '::' (git remote helpers such as ext:: are refused)"
    return None


def _check_url(url: str, scheme: str, rest: str) -> str:
    if scheme not in ("https", "ssh"):
        name = f"{scheme}://" if _SCHEME.fullmatch(scheme) else "this URL scheme"
        raise TargetError(f"refused URL scheme {name}: use an https:// or ssh:// git URL")
    authority = rest.split("/", 1)[0]
    if "@" in authority and (scheme == "https" or ":" in authority.rsplit("@", 1)[0]):
        raise TargetError(CREDENTIALS)
    if not (_HTTPS_URL if scheme == "https" else _SSH_URL).fullmatch(url):
        raise TargetError(f"not a valid {scheme} git URL (expected {scheme}://host/owner/repo)")
    return url


def _looks_like_scp(text: str) -> bool:
    """git's own rule: `host:path` and `user@host:path` are ssh when the first colon comes before any slash."""
    colon, slash = text.find(":"), text.find("/")
    return colon > 0 and (slash < 0 or colon < slash)


def _check_path(text: str) -> str:
    """The absolute path of the repository at TEXT; it must be the top folder of a work tree, or a bare repository."""
    path = Path(os.path.expanduser(text))
    if not path.exists():
        guess = f"; did you mean https://{text}?" if re.match(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/", text) else ""
        raise TargetError(f"no such folder: {text} (SOURCE is {SOURCE_HELP}){guess}")
    if not path.is_dir():
        raise TargetError(f"not a folder: {text}")
    top = _git_line(path, "rev-parse", "--show-toplevel")
    if top and os.path.samefile(top, path):
        return str(path.resolve())
    if top:
        raise TargetError(f"{text} is inside the git repository {top}, not its top folder; pass {top} if that is the project")
    git_dir = _git_line(path, "rev-parse", "--absolute-git-dir")
    if _git_line(path, "rev-parse", "--is-bare-repository") == "true" and git_dir and os.path.samefile(git_dir, path):
        return str(path.resolve())
    raise TargetError(f"not a git repository: {text}")


def _git_line(project: Path | str, *args: str) -> str:
    """The output of a small git command, stripped; empty when it fails."""
    try:
        return gitutil.run(project, *args).strip()
    except gitutil.GitError:
        return ""


def check_branch(name: str, where: Path) -> str:
    """NAME when `git check-ref-format --branch` accepts it unchanged, else raise TargetError."""
    if name and not _unsafe(name) and _git_line(where, "check-ref-format", "--branch", name) == name:
        return name
    raise TargetError(f"not a valid branch name: {name!r}")


def _into(text: str) -> Path:
    """DIR as an absolute path. Refused when moving it away would move the current folder or the checker."""
    dest = Path(os.path.abspath(os.path.expanduser(text)))  # not resolved: a symlink DIR is moved, not its target
    if not dest.name:
        raise TargetError(f"--into must name a folder: {text}")
    real = dest.resolve()
    for inner, what in ((Path.cwd().resolve(), "the current folder"), (ROOT, "the checker itself")):
        if real == inner or real in inner.parents:
            raise TargetError(f"--into {text} would move {what} away; choose a folder for the project, such as {DEFAULT_INTO}")
    return dest


def load(source: Source, dest: Path, branch: str | None = None) -> tuple[dict[str, Any], Path | None]:
    """Clone (or make) the project next to DEST, then swap it in.

    Returns the target record and where the old project was moved (None when there was none). On any
    error the old project is untouched and only the temporary folder is removed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if branch is not None:
        check_branch(branch, dest.parent)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.loading-", dir=dest.parent))
    try:
        work = staging / dest.name  # a subfolder, so the project gets normal permissions (mkdtemp makes 0700)
        if source.kind == "sample":
            _make_sample(work)
        else:
            _clone(source, work, branch)
        record = _record(source, work)
        _ignore_own_output(work)
        write_json(record, work / gitutil.IGNORED_DIR / TARGET_FILE)
        return record, _swap(work, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _make_sample(work: Path) -> None:
    """A fresh sample with 6 planted defects, made the way scripts/reset-demo.sh makes it."""
    if not MAKE_SAMPLE.is_file():
        raise TargetError(f"cannot make the sample: {MAKE_SAMPLE} is missing")
    try:
        proc = subprocess.run(
            [sys.executable, str(MAKE_SAMPLE), str(work)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SAMPLE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetError(f"cannot make the sample: {exc}") from exc
    if proc.returncode != 0:
        raise TargetError(f"cannot make the sample: {_last_line(proc.stderr) or f'exit {proc.returncode}'}")


def _clone(source: Source, work: Path, branch: str | None) -> None:
    """A full clone of SOURCE into WORK, without submodules, that never waits for a password."""
    argv = ["git", "clone", "--origin", "origin", "--no-recurse-submodules"]
    if source.kind == "path":
        argv.append("--no-local")  # copy reachable commits only, as a URL clone would, with no hard links
    if branch is not None:
        argv += ["--branch", branch]
    argv += ["--", source.value, str(work)]
    try:
        code, stderr = _run(argv, _source_env(source.kind), CLONE_TIMEOUT)
    except FileNotFoundError as exc:
        raise TargetError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise TargetError(f"cannot clone {source.value}: no result after {CLONE_TIMEOUT:.0f} s, so it was stopped") from exc
    if code != 0:
        raise TargetError(f"cannot clone {source.value}: {_clone_problem(stderr, source, branch)}")


def _source_env(kind: str) -> dict[str, str]:
    """The environment for git talking to SOURCE: never a prompt, and only the transport that kind needs."""
    env = dict(os.environ)
    env.update(
        GIT_TERMINAL_PROMPT="0",  # git never asks for a user name or password
        GIT_ASKPASS="",  # nor in a password window (an empty value turns core.askPass and SSH_ASKPASS off)
        SSH_ASKPASS_REQUIRE="never",
        GCM_INTERACTIVE="never",  # Git Credential Manager: no sign-in window
        GIT_ALLOW_PROTOCOL="file" if kind == "path" else "https:ssh",  # also refuses redirects elsewhere
    )
    return env


def _run(argv: list[str], env: dict[str, str], timeout: float) -> tuple[int, str]:
    """Run a command with no input and no terminal; return (exit code, stderr).

    It runs in a session of its own: ssh cannot ask for a password or a host key on the terminal, and
    on a timeout (or Ctrl-C) the whole group, git and the ssh or https helper it started, is stopped.
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.communicate()
        raise
    return proc.returncode, stderr


def _clone_problem(stderr: str, source: Source, branch: str | None) -> str:
    """One plain line that says why the clone failed, from git's error output."""
    text = stderr.lower()
    if branch is not None and "remote branch" in text and "not found" in text:
        return f"branch {branch} not found"
    if any(word in text for word in (
        "could not resolve host", "couldn't connect", "failed to connect", "connection refused",
        "connection timed out", "operation timed out", "network is unreachable",
    )):
        return "cannot reach the server; check the network and the URL"
    if "host key verification failed" in text:
        return "the server's ssh host key is not known yet; connect once with ssh to accept it, or use the https URL"
    if source.kind == "url" and any(word in text for word in (
        "not found", "could not read username", "could not read password", "authentication failed",
        "terminal prompts disabled", "permission denied", "could not read from remote repository",
        "access denied", "returned error: 40",
    )):
        hint = LOGIN_HINT if parse_github_remote(source.value) else "check the URL and that your git credentials can read it"
        return f"repository not found or private; {hint}"
    return re.sub(r"^(?:fatal|error): ", "", _last_line(stderr)) or "git clone failed"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _record(source: Source, work: Path) -> dict[str, Any]:
    """What target.json says about the loaded project."""
    commit = gitutil.head_sha(work)
    if commit is None:
        raise TargetError(
            f"{source.value} has no commit to check out (an empty repository, or its default branch is missing; try --branch NAME)"
        )
    if source.kind == "path":
        _mirror_local(Path(source.value), work)
    origin = gitutil.origin_url(work)
    return {
        "source": source.value,
        "kind": source.kind,
        "branch": _branch(work),
        "commit": commit,
        "origin": safe_url(origin) if origin else None,
        "loaded_at": now_iso(),
    }


def _mirror_local(repo: Path, work: Path) -> None:
    """Make the clone of a local repository match it as seen from outside.

    The clone gets the repository's origin URL (so checks such as repo-public see the real GitHub repo),
    its local branches, origin/* where the repository last saw them on that remote (not its local
    branches, which a plain clone files there), and the same upstream. So "is this commit pushed?"
    gets the same answer in both. Without a usable origin the clone keeps the local path as its origin,
    and origin/* are that path's branches: also true.
    """
    url = gitutil.origin_url(repo)
    if url is None or _unsafe(url):
        return
    if "://" not in url and not _looks_like_scp(url) and not os.path.isabs(url):
        url = str((repo / url).resolve())  # a relative path is relative to that repository, not to the clone
    argv = ["git", "-C", str(work), "-c", "gc.auto=0", "-c", "maintenance.auto=false", "fetch", "--quiet", "--no-tags",
            "--no-recurse-submodules", "--update-head-ok", "--prune", "--", str(repo),
            "+refs/heads/*:refs/heads/*", "+refs/remotes/origin/*:refs/remotes/origin/*"]
    cloned = gitutil.head_sha(work)
    try:
        code, stderr = _run(argv, _source_env("path"), CLONE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise TargetError(f"cannot copy the branches of {repo}: no result after {CLONE_TIMEOUT:.0f} s") from exc
    if code != 0:
        raise TargetError(f"cannot copy the branches of {repo}: {_last_line(stderr) or f'exit {code}'}")
    if gitutil.head_sha(work) != cloned:  # someone committed there while it was being cloned
        raise TargetError(f"{repo} changed while it was being loaded; run the command again")
    if not _git_line(work, "rev-parse", "-q", "--verify", "refs/remotes/origin/HEAD"):
        _git_line(work, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")  # it named a pruned branch
    branch = _branch(work)
    if branch:
        merge = _git_line(repo, "config", "--get", f"branch.{branch}.merge")
        if _git_line(repo, "config", "--get", f"branch.{branch}.remote") == "origin" and merge.startswith("refs/"):
            gitutil.run(work, "config", f"branch.{branch}.merge", merge)
        else:  # never pushed: no upstream, as in the repository
            _git_line(work, "config", "--unset", f"branch.{branch}.remote")
            _git_line(work, "config", "--unset", f"branch.{branch}.merge")
    gitutil.run(work, "remote", "set-url", "--", "origin", safe_url(url))


def safe_url(url: str) -> str:
    """URL without a password or token. An ssh user such as git@ is kept: it is not a secret."""
    if url.startswith("ssh://"):
        authority = url[len("ssh://"):].split("/", 1)[0]
        if "@" in authority and ":" not in authority.rsplit("@", 1)[0]:
            return url
    return redact_url(url)


def _branch(project: Path) -> str | None:
    """The checked-out branch, or None for a detached HEAD (or no repository)."""
    return _git_line(project, "symbolic-ref", "--short", "-q", "HEAD") or None


def _ignore_own_output(work: Path) -> None:
    """Add /.preflight/ to the clone's own .git/info/exclude, so `git add -A` never commits Preflight's output."""
    exclude = work / gitutil.run(work, "rev-parse", "--git-path", "info/exclude").strip()
    entry = f"/{gitutil.IGNORED_DIR}/"
    text = exclude.read_text(encoding="utf-8", errors="replace") if exclude.is_file() else ""
    if entry in text.splitlines():
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write(("\n" if text and not text.endswith("\n") else "") + entry + "\n")


def _swap(work: Path, dest: Path) -> Path | None:
    """Move the old DEST (if any) to .old-projects/<name>-<YYYYmmdd-HHMMSS>, then rename WORK to DEST."""
    kept = None
    if os.path.lexists(dest):
        shelf = dest.parent / OLD_PROJECTS
        shelf.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        kept = shelf / f"{dest.name}-{stamp}"
        number = 2
        while os.path.lexists(kept):
            kept = shelf / f"{dest.name}-{stamp}-{number}"
            number += 1
        os.rename(dest, kept)
    try:
        os.rename(work, dest)
    except OSError:
        if kept is not None:
            os.rename(kept, dest)  # put the old project back
        raise
    return kept


def read_record(dest: Path) -> dict[str, Any] | None:
    """DIR/.preflight/target.json, or None when it is missing or unreadable."""
    try:
        record = json.loads((dest / gitutil.IGNORED_DIR / TARGET_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("source"), str) else None


def _state(branch: Any, commit: Any) -> str:
    """'branch main, commit 1a2b3c4' (or 'detached HEAD, ...')."""
    commit = commit if isinstance(commit, str) else None
    if not isinstance(branch, str) or not branch:
        return "no commit" if commit is None else f"detached HEAD, commit {gitutil.short_sha(commit)}"
    return f"branch {branch}, commit {gitutil.short_sha(commit)}"


def show(dest: Path, shown: str) -> int:
    """Print what DIR holds: the TARGET line, and a `now:` line when HEAD moved since loading."""
    if not dest.is_dir():
        print("TARGET: none (run kit/pf target <url|path|sample>)")
        return 1
    branch, commit = _branch(dest), gitutil.head_sha(dest)
    record = read_record(dest)
    if record is not None:
        print(f"TARGET: {safe_url(record['source'])} ({_state(record.get('branch'), record.get('commit'))}) -> {shown}/")
        if (branch, commit) != (record.get("branch"), record.get("commit")):
            print(f"now: {_state(branch, commit)}")
        return 0
    top = _git_line(dest, "rev-parse", "--show-toplevel")
    if not top or not os.path.samefile(top, dest):
        print(f"TARGET: none ({shown}/ is not a git repository; run kit/pf target <url|path|sample>)")
        return 1
    origin = gitutil.origin_url(dest)
    print(f"TARGET: {safe_url(origin) if origin else 'unknown (no origin remote)'} ({_state(branch, commit)}) -> {shown}/")
    print(f"note: not loaded with kit/pf target ({gitutil.IGNORED_DIR}/{TARGET_FILE} is missing)")
    return 0


def _error(message: str) -> int:
    print(f"preflight: error: {message}", file=sys.stderr)
    return 2


def cmd_target(args: argparse.Namespace) -> int:
    """Load SOURCE into DIR, or with --show say what DIR holds now."""
    shown = os.path.normpath(args.into)
    if args.show:
        if args.source is not None or args.branch is not None:
            return _error("--show takes no SOURCE and no --branch")
        return show(Path(os.path.abspath(os.path.expanduser(args.into))), shown)
    if args.source is None:
        return _error(f"give SOURCE ({SOURCE_HELP}), or --show")
    try:
        dest = _into(args.into)
        source = parse_source(args.source)
        if source.kind == "sample" and args.branch is not None:
            raise TargetError("--branch does not apply to sample (it has one branch, main)")
        record, kept = load(source, dest, args.branch)
    except TargetError as exc:
        return _error(str(exc))
    except (OSError, gitutil.GitError) as exc:
        return _error(f"cannot load the project into {shown}/: {exc}")
    print(f"TARGET: {record['source']} ({_state(record['branch'], record['commit'])}) -> {shown}/")
    if kept is not None:
        print(f"old project kept in {os.path.join(os.path.dirname(shown), OLD_PROJECTS, kept.name)}")
    return 0


def add_parser(sub: Any) -> None:
    parser = sub.add_parser(
        "target",
        help="choose the project under test: clone a git URL, a local repository or the sample into project/",
        description="Load the project under test into DIR (default: project). The old DIR is kept in .old-projects/.",
    )
    parser.add_argument("source", nargs="?", metavar="SOURCE", help=f"{SOURCE_HELP} (a fresh sample with 6 planted defects)")
    parser.add_argument("--into", default=DEFAULT_INTO, metavar="DIR", help=f"folder to load it into (default: {DEFAULT_INTO})")
    parser.add_argument("--branch", metavar="NAME", help="branch to check out (default: the repository's default branch)")
    parser.add_argument("--show", action="store_true", help="print what DIR holds now; change nothing")
    parser.set_defaults(func=cmd_target)
