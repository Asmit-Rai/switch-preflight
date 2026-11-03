"""Shared test helpers: throwaway git repos, a local web server, small builders.

Tests stay offline: network checks talk to a server on 127.0.0.1 or to an injected fake `fetch`.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

for _name in ("no_proxy", "NO_PROXY"):  # a system proxy must never intercept the local test server
    os.environ[_name] = ",".join(filter(None, [os.environ.get(_name), "127.0.0.1,localhost"]))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from preflight import cli, net  # noqa: E402
from preflight.checks import CHECK_TYPES, Context, run_one  # noqa: E402
from preflight.model import CheckSpec, Outcome  # noqa: E402

RULES = ROOT / "rules"
EXAMPLES = ROOT / "examples"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
AFTER = "2026-11-04T10:00:00+05:30"  # after the contest start (2026-11-03T18:31:00Z)
BEFORE = "2026-11-02T10:00:00+05:30"  # before it


def git_env(when: str | None = None) -> dict[str, str]:
    """Environment that ignores the machine's git config and fixes the identity and date."""
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.com",
    )
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return env


def git(repo: Path, *args: str, when: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, env=git_env(when), check=True,
    )
    return proc.stdout.strip()


def write(repo: Path, rel: str, text: str | bytes) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def commit_all(repo: Path, message: str = "change", when: str = AFTER) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, when=when)
    return git(repo, "rev-parse", "HEAD")


def make_repo(path: Path, files: dict[str, str | bytes] | None = None, when: str = AFTER, commit: bool = True) -> Path:
    """A fresh git repo on branch main, with `files` written and committed."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    for rel, text in (files or {}).items():
        write(path, rel, text)
    if commit and files:
        commit_all(path, "initial", when)
    return path


class RepoCase(unittest.TestCase):
    """A test case with a temp folder that is removed afterwards."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self._repos = 0

    def repo(self, files: dict[str, str | bytes] | None = None, name: str | None = None, when: str = AFTER) -> Path:
        """A new committed repo in its own folder (proj1, proj2, ... unless `name` is given)."""
        self._repos += 1
        return make_repo(self.tmp / (name or f"proj{self._repos}"), files, when)

    def check(self, repo: Path, check_type: str, *, offline: bool = False, fetch: Callable[..., net.Response] | None = None,
              env: dict[str, str] | None = None, required: bool = True, **params: Any) -> Outcome:
        """Run one check of `check_type` with `params` against `repo`."""
        spec = CheckSpec("t", "test check", check_type, required, "", params)
        ctx = Context(repo, offline=offline, fetch=fetch or no_network, env=env if env is not None else dict(os.environ))
        return run_one(spec, ctx)

    def assertStatus(self, outcome: Outcome, status: str, *words: str) -> None:
        self.assertEqual(outcome.status, status, outcome.detail)
        for word in words:
            self.assertIn(word, outcome.detail)


def no_network(url: str, **kwargs: Any) -> net.Response:
    """A fetch that fails the test if any code reaches for the network."""
    raise AssertionError(f"unexpected network call: {url}")


def run_cli(*argv: str) -> tuple[int, str, str]:
    """Run the CLI in-process. Returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:  # argparse exits with 2 on usage errors
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def run_module(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run `python3 -m preflight ...` for real, from the repo root."""
    return subprocess.run(
        [sys.executable, "-m", "preflight", *argv], capture_output=True, text=True, cwd=cwd or ROOT, timeout=120,
    )


def read_verdict(project: Path) -> dict[str, Any]:
    return json.loads((project / ".preflight" / "verdict.json").read_text(encoding="utf-8"))


class LocalWeb:
    """A tiny local HTTP server. `routes` maps a path to (status, headers, body).

    A route whose value is a dict maps a method to such a tuple, so HEAD and GET can differ.
    """

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                outer.requests.append((self.command, self.path))
                route = outer.routes.get(self.path.split("?")[0])
                if isinstance(route, dict):
                    route = route.get(self.command, route.get("*"))
                status, headers, body = route or (404, {}, b"not found")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                data = body.encode() if isinstance(body, str) else body
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            do_GET = do_HEAD = _serve

            def log_message(self, *args: Any) -> None:  # keep test output quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "LocalWeb":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@contextmanager
def local_web(routes: dict[str, Any]) -> Iterator[LocalWeb]:
    with LocalWeb(routes) as web:
        yield web


def closed_port_url() -> str:
    """A URL on 127.0.0.1 where nothing listens (connection refused)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.server_close()
    return f"http://127.0.0.1:{port}/nothing"


def register_check(name: str, func: Callable[..., Outcome]) -> None:
    """Temporarily add a check type (the caller must patch CHECK_TYPES back; see test_checks)."""
    from preflight.checks.base import CheckType

    CHECK_TYPES[name] = CheckType(name, func, {}, "test only")
