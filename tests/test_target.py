"""`preflight target`: load the project under test (local repository, URL or sample) safely; `--show` reads it."""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from helpers import RepoCase, closed_port_url, commit_all, git, make_repo, run_cli, run_module, write  # also puts examples/ on sys.path

import make_sample  # noqa: E402
from preflight import target  # noqa: E402
from preflight.timeutil import parse_timestamp  # noqa: E402

GITHUB = "https://github.com/team/app.git"
LEAKED = "https://someone:hunter2@github.com/team/app.git"  # a password in a remote URL: must never be stored
NOTHING = "TARGET: none (run kit/pf target <url|path|sample>)\n"
PRIVATE = (
    "repository not found or private; sign in with gh auth login or grant the agent a GitHub connection"
)

# Each refused SOURCE and the words its one-line error must hold.
REFUSED = {
    "-oProxyCommand=touch": "must not start with '-'",
    "--upload-pack=touch": "must not start with '-'",
    "https://github.com/team/my app.git": "must not contain spaces or control characters",
    "https://github.com/team/app.git\n": "must not contain spaces or control characters",
    "https://github.com/team/\tapp": "must not contain spaces or control characters",
    "https://github.com/team/app\x1b[0m": "must not contain spaces or control characters",
    "ext::sh": "must not contain '::'",
    "fd::17": "must not contain '::'",
    "file:///tmp/repo": "refused URL scheme file://",
    "http://github.com/team/app.git": "refused URL scheme http://",
    "git://github.com/team/app.git": "refused URL scheme git://",
    "git+ssh://git@github.com/team/app.git": "refused URL scheme git+ssh://",
    "ftp://example.com/app.git": "refused URL scheme ftp://",
    LEAKED: "carries credentials",
    "https://abc123@github.com/team/app.git": "carries credentials",
    "ssh://git:hunter2@github.com/team/app.git": "carries credentials",
    "someone:hunter2@github.com:team/app.git": "carries credentials",
    "https://github.com/team/app?ref=main": "not a valid https git URL",
    "https://github.com": "not a valid https git URL",
    "git@github.com:": "not a valid ssh git URL",
}

ACCEPTED_URLS = [
    "https://github.com/team/app",
    "https://github.com/team/app.git",
    "https://github.com/team/app/",
    "https://gitlab.example.com:8443/group/sub/app.git",
    "git@github.com:team/app.git",
    "ssh://git@github.com/team/app.git",
    "ssh://git@github.com:22/team/app.git",
    "github_work:team/app.git",  # an ssh config alias
]


class TargetCase(RepoCase):
    """A shared folder (where DIR goes) next to source repositories; git ignores this machine's config."""

    def setUp(self) -> None:
        super().setUp()
        quiet_git = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = mock.patch.dict(os.environ, quiet_git)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.shared = self.tmp / "shared"
        self.shared.mkdir()
        self.dest = self.shared / "project"

    def source(self, origin: str | None = GITHUB, name: str = "src") -> tuple[Path, str]:
        """A committed repository with `origin` set; returns (path, HEAD sha)."""
        repo = self.repo({"README.md": "# App\n", "app.py": "print('hi')\n"}, name=name)
        if origin:
            git(repo, "remote", "add", "origin", origin)
        return repo, git(repo, "rev-parse", "HEAD")

    def old_project(self) -> str:
        """An existing project in DIR; returns its HEAD sha."""
        return git(make_repo(self.dest, {"old.txt": "the old project\n"}), "rev-parse", "HEAD")

    def target(self, *argv: str) -> tuple[int, str, str]:
        return run_cli("target", *argv, "--into", str(self.dest))

    def show(self) -> tuple[int, str, str]:
        return run_cli("target", "--show", "--into", str(self.dest))

    def record(self) -> dict[str, Any]:
        return json.loads((self.dest / ".preflight" / "target.json").read_text(encoding="utf-8"))

    def leftovers(self) -> list[str]:
        """Temporary folders left next to DIR: there must never be any."""
        return sorted(p.name for p in self.shared.iterdir() if ".loading-" in p.name)

    def assertUntouched(self, old_head: str) -> None:
        self.assertEqual(git(self.dest, "rev-parse", "HEAD"), old_head)
        self.assertTrue((self.dest / "old.txt").is_file())
        self.assertFalse((self.shared / ".old-projects").exists())
        self.assertEqual(self.leftovers(), [])


class LoadLocalRepositoryTests(TargetCase):
    def test_clone_copies_the_origin_and_records_what_was_loaded(self) -> None:
        src, sha = self.source()
        code, out, err = self.target(str(src))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"TARGET: {src} (branch main, commit {sha[:7]}) -> {self.dest}/\n")
        self.assertEqual(git(self.dest, "rev-parse", "HEAD"), sha)
        self.assertEqual(git(self.dest, "remote", "get-url", "origin"), GITHUB)  # so repo-public sees the real repo

        record = self.record()
        self.assertEqual(set(record), {"source", "kind", "branch", "commit", "origin", "loaded_at"})
        self.assertEqual(
            {key: record[key] for key in ("source", "kind", "branch", "commit", "origin")},
            {"source": str(src), "kind": "path", "branch": "main", "commit": sha, "origin": GITHUB},
        )
        self.assertIsNotNone(parse_timestamp(record["loaded_at"]).tzinfo)
        # Preflight's own output is not a change, and `git add -A` in the project will not pick it up.
        self.assertEqual(git(self.dest, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.shared / ".old-projects").exists())

    def test_the_clone_knows_what_the_repository_knows_about_its_remote(self) -> None:
        src, pushed = self.source()
        git(src, "update-ref", "refs/remotes/origin/main", pushed)  # as after `git push -u origin main`
        git(src, "config", "branch.main.remote", "origin")
        git(src, "config", "branch.main.merge", "refs/heads/main")
        write(src, "app.py", "print('local work')\n")
        local = commit_all(src, "not pushed yet")
        git(src, "branch", "side", pushed)
        self.assertEqual(self.target(str(src))[0], 0)

        # origin/* is where the repository last saw GitHub, not a copy of its local branches
        self.assertEqual(git(self.dest, "rev-parse", "refs/remotes/origin/main"), pushed)
        self.assertEqual(git(self.dest, "for-each-ref", "refs/remotes/origin/side"), "")
        self.assertEqual(git(self.dest, "branch", "-r", "--contains", local), "")  # the new commit is not on GitHub
        self.assertIn("origin/main", git(self.dest, "branch", "-r", "--contains", pushed))
        self.assertEqual(git(self.dest, "rev-parse", "side"), pushed)  # the other local branches came along
        self.assertEqual(git(self.dest, "status", "-sb").splitlines()[0], "## main...origin/main [ahead 1]")

    def test_a_repository_that_was_never_pushed_has_no_upstream_in_the_clone(self) -> None:
        src, _ = self.source()
        self.assertEqual(self.target(str(src))[0], 0)
        self.assertEqual(git(self.dest, "for-each-ref", "refs/remotes"), "")
        self.assertEqual(git(self.dest, "status", "-sb").splitlines()[0], "## main")

    def test_credentials_in_the_source_origin_are_never_stored(self) -> None:
        src, _ = self.source(origin=LEAKED)
        self.assertEqual(self.target(str(src))[0], 0)
        self.assertEqual(git(self.dest, "remote", "get-url", "origin"), GITHUB)
        self.assertEqual(self.record()["origin"], GITHUB)
        for path in (self.dest / ".preflight" / "target.json", self.dest / ".git" / "config"):
            self.assertNotIn("hunter2", path.read_text(encoding="utf-8"), path)

    def test_an_ssh_origin_keeps_its_user(self) -> None:
        for number, origin in enumerate(("git@github.com:team/app.git", "ssh://git@github.com/team/app.git")):
            src, _ = self.source(origin=origin, name=f"src{number}")
            self.assertEqual(self.target(str(src))[0], 0)
            self.assertEqual(git(self.dest, "remote", "get-url", "origin"), origin)
            self.assertEqual(self.record()["origin"], origin)

    def test_a_repository_without_origin_keeps_the_local_path_as_origin(self) -> None:
        src, _ = self.source(origin=None)
        self.assertEqual(self.target(str(src))[0], 0)
        self.assertEqual(self.record()["origin"], str(src))

    def test_a_bare_repository_can_be_loaded(self) -> None:
        src, sha = self.source()
        bare = self.tmp / "app.git"
        git(self.tmp, "clone", "-q", "--bare", str(src), str(bare))
        code, out, _ = self.target(str(bare))
        self.assertEqual(code, 0)
        self.assertEqual(out, f"TARGET: {bare} (branch main, commit {sha[:7]}) -> {self.dest}/\n")

    def test_relative_paths_are_shown_as_given(self) -> None:
        src, sha = self.source()
        old_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, old_cwd)
        code, out, _ = run_cli("target", "src", "--into", "shared/project")
        self.assertEqual((code, out), (0, f"TARGET: {src} (branch main, commit {sha[:7]}) -> shared/project/\n"))
        code, out, _ = run_cli("target", "src", "--into", "shared/project")
        self.assertEqual(code, 0)
        self.assertRegex(out.splitlines()[1], r"^old project kept in shared/\.old-projects/project-\d{8}-\d{6}$")

    def test_a_subfolder_of_a_repository_is_refused(self) -> None:
        src = self.repo({"README.md": "# x\n", "sub/a.txt": "a\n"}, name="src")
        code, out, err = self.target(str(src / "sub"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn(f"is inside the git repository {src}, not its top folder", err)
        self.assertFalse(self.dest.exists())


class OldProjectTests(TargetCase):
    def test_the_old_project_is_moved_to_old_projects_never_deleted(self) -> None:
        src, sha = self.source()
        self.dest.mkdir()
        (self.dest / "notes.txt").write_text("mine\n", encoding="utf-8")
        code, out, _ = self.target(str(src))
        self.assertEqual(code, 0)
        shelf = self.shared / ".old-projects"
        kept = sorted(shelf.iterdir())
        self.assertEqual(len(kept), 1)
        self.assertRegex(kept[0].name, r"^project-\d{8}-\d{6}$")
        self.assertEqual(
            out.splitlines(),
            [f"TARGET: {src} (branch main, commit {sha[:7]}) -> {self.dest}/", f"old project kept in {kept[0]}"],
        )
        self.assertEqual((kept[0] / "notes.txt").read_text(encoding="utf-8"), "mine\n")

        # Two more loads in the same second: each old project gets a folder of its own.
        self.assertEqual(self.target(str(src))[0], 0)
        self.assertEqual(self.target(str(src))[0], 0)
        self.assertEqual(len(list(shelf.iterdir())), 3)
        self.assertEqual(self.leftovers(), [])

    def test_a_failed_clone_keeps_the_old_project(self) -> None:
        src, _ = self.source()
        old_head = self.old_project()
        code, out, err = self.target(str(src), "--branch", "nope")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, f"preflight: error: cannot clone {src}: branch nope not found\n")
        self.assertUntouched(old_head)

    def test_a_private_repository_fails_at_once_with_one_clear_line(self) -> None:
        old_head = self.old_project()
        calls: list[tuple[list[str], dict[str, str], float]] = []

        def private(argv: list[str], env: dict[str, str], timeout: float) -> tuple[int, str]:
            calls.append((argv, env, timeout))
            return 128, (
                "Cloning into 'project'...\n"
                "fatal: could not read Username for 'https://github.com': terminal prompts disabled\n"
            )

        with mock.patch.object(target, "_run", private):
            code, out, err = self.target("https://github.com/team/private-app")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, f"preflight: error: cannot clone https://github.com/team/private-app: {PRIVATE}\n")
        self.assertUntouched(old_head)

        [(argv, env, timeout)] = calls
        self.assertEqual(argv[:2], ["git", "clone"])
        self.assertIn("--no-recurse-submodules", argv)
        self.assertFalse(any(arg.startswith(("--depth", "--shallow", "--single-branch")) for arg in argv), argv)
        self.assertEqual(argv[-3:-1], ["--", "https://github.com/team/private-app"])
        staging = Path(argv[-1])  # the clone goes to a temporary folder next to DIR
        self.assertEqual((staging.name, staging.parent.parent), ("project", self.shared))
        self.assertTrue(staging.parent.name.startswith(".project.loading-"))
        self.assertEqual((env["GIT_TERMINAL_PROMPT"], env["GIT_ALLOW_PROTOCOL"]), ("0", "https:ssh"))
        self.assertEqual(timeout, 600)

    def test_an_unreachable_server_fails_fast(self) -> None:
        port = closed_port_url().split(":")[2].split("/")[0]
        url = f"https://127.0.0.1:{port}/team/app.git"
        old_head = self.old_project()
        with mock.patch.object(target, "CLONE_TIMEOUT", 60.0):
            code, out, err = self.target(url)
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, f"preflight: error: cannot clone {url}: cannot reach the server; check the network and the URL\n")
        self.assertUntouched(old_head)

    def test_a_timeout_stops_git_and_everything_it_started(self) -> None:
        # The child starts a grandchild that holds stderr open. Unless the whole group is stopped,
        # reading stderr after the timeout would wait for the grandchild (60 s).
        child = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
        )
        started = time.monotonic()
        with self.assertRaises(target.subprocess.TimeoutExpired):
            target._run([sys.executable, "-c", child], dict(os.environ), 1.0)
        self.assertLess(time.monotonic() - started, 15)

    def test_clone_errors_become_one_plain_line(self) -> None:
        github = target.Source("url", "https://github.com/team/app")
        ssh = target.Source("url", "git@github.com:team/app.git")
        other = target.Source("url", "https://git.example.com/team/app.git")
        cases = [
            (github, "remote: Repository not found.\nfatal: repository 'https://github.com/team/app/' not found\n", PRIVATE),
            (ssh, "ERROR: Repository not found.\nfatal: Could not read from remote repository.\n\nPlease make sure you "
                  "have the correct access rights\nand the repository exists.\n", PRIVATE),
            (ssh, "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.\n", PRIVATE),
            (other, "fatal: Authentication failed for 'https://git.example.com/team/app.git/'\n",
             "repository not found or private; check the URL and that your git credentials can read it"),
            (github, "fatal: unable to access 'https://github.com/team/app/': Could not resolve host: github.com\n",
             "cannot reach the server; check the network and the URL"),
            (ssh, "Host key verification failed.\nfatal: Could not read from remote repository.\n",
             "the server's ssh host key is not known yet; connect once with ssh to accept it, or use the https URL"),
            (github, "fatal: something odd happened\n", "something odd happened"),
            (github, "", "git clone failed"),
        ]
        for source, stderr, line in cases:
            self.assertEqual(target._clone_problem(stderr, source, None), line, stderr)
        self.assertEqual(target._clone_problem("fatal: Remote branch x not found in upstream origin\n", github, "x"), "branch x not found")


class BranchTests(TargetCase):
    def test_the_named_branch_is_checked_out(self) -> None:
        src, _ = self.source()
        git(src, "checkout", "-q", "-b", "feature")
        write(src, "feature.txt", "x\n")
        feature = commit_all(src, "feature work")
        git(src, "checkout", "-q", "main")
        code, out, _ = self.target(str(src), "--branch", "feature")
        self.assertEqual((code, out), (0, f"TARGET: {src} (branch feature, commit {feature[:7]}) -> {self.dest}/\n"))
        self.assertEqual((self.record()["branch"], self.record()["commit"]), ("feature", feature))
        self.assertEqual(git(self.dest, "rev-parse", "HEAD"), feature)

    def test_invalid_branch_names_are_refused_before_any_clone(self) -> None:
        src, _ = self.source()
        for name in ("a b", "a..b", "@{-1}", "-x", "x~1", "HEAD", "a:b", ""):
            code, out, err = self.target(str(src), f"--branch={name}")
            self.assertEqual((code, out), (2, ""), name)
            self.assertEqual(err, f"preflight: error: not a valid branch name: {name!r}\n")
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.leftovers(), [])


class SampleTests(TargetCase):
    def test_sample_is_a_fresh_sample_with_the_planted_defects(self) -> None:
        code, out, err = self.target("sample")
        self.assertEqual((code, err), (0, ""))
        reference = self.tmp / "reference"
        make_sample.create_sample(reference)  # the same commits as scripts/reset-demo.sh makes
        sha = git(reference, "rev-parse", "HEAD")
        self.assertEqual(out, f"TARGET: sample (branch main, commit {sha[:7]}) -> {self.dest}/\n")
        record = self.record()
        self.assertEqual(
            {key: record[key] for key in ("source", "kind", "branch", "commit", "origin")},
            {"source": "sample", "kind": "sample", "branch": "main", "commit": sha, "origin": make_sample.ORIGIN},
        )
        self.assertIn("GITHUB_TOKEN", (self.dest / "settings_local.py").read_text(encoding="utf-8"))
        self.assertFalse((self.dest / "LICENSE").exists())
        self.assertEqual(git(self.dest, "status", "--porcelain", "--untracked-files=all"), "")

    def test_branch_does_not_apply_to_sample(self) -> None:
        code, _, err = self.target("sample", "--branch", "main")
        self.assertEqual(code, 2)
        self.assertIn("--branch does not apply to sample", err)
        self.assertFalse(self.dest.exists())


class RefusedSourceTests(TargetCase):
    def test_each_refused_form_exits_2_with_one_line_and_changes_nothing(self) -> None:
        old_head = self.old_project()
        for source, words in REFUSED.items():
            with self.subTest(source=source):
                code, out, err = run_cli("target", "--into", str(self.dest), "--", source)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith("preflight: error: "), err)
                self.assertIn(words, err)
                self.assertEqual(err.count("\n"), 1, err)
                self.assertNotIn("hunter2", err)  # a refused URL with a password is never echoed
        self.assertUntouched(old_head)

    def test_well_formed_urls_are_accepted(self) -> None:
        for url in ACCEPTED_URLS:
            self.assertEqual(target.parse_source(url), target.Source("url", url))

    def test_paths_that_are_not_repositories_are_refused(self) -> None:
        (self.tmp / "plain").mkdir()
        (self.tmp / "file.txt").write_text("x\n", encoding="utf-8")
        self.repo({"README.md": "# x\n"}, name="my app")
        cases = {
            str(self.tmp / "nowhere"): "no such folder",
            "github.com/team/app": "did you mean https://github.com/team/app?",
            str(self.tmp / "file.txt"): "not a folder",
            str(self.tmp / "plain"): "not a git repository",
            str(self.tmp / "my app"): "must not contain spaces",  # SOURCE never holds whitespace, a path neither
        }
        for source, words in cases.items():
            code, _, err = self.target(source)
            self.assertEqual(code, 2, source)
            self.assertIn(words, err)
        self.assertFalse(self.dest.exists())


class ShowTests(TargetCase):
    def test_show_after_loading_and_after_new_work_in_the_project(self) -> None:
        src, sha = self.source()
        self.target(str(src))
        loaded = f"TARGET: {src} (branch main, commit {sha[:7]}) -> {self.dest}/"
        self.assertEqual(self.show(), (0, loaded + "\n", ""))

        write(self.dest, "app.py", "print('fixed')\n")
        fixed = commit_all(self.dest, "fix")
        self.assertEqual(git(self.dest, "ls-files", ".preflight"), "")  # `git add -A` left target.json alone
        code, out, _ = self.show()
        self.assertEqual((code, out.splitlines()), (0, [loaded, f"now: branch main, commit {fixed[:7]}"]))

        git(self.dest, "checkout", "-q", "-b", "preflight/fix-1")
        self.assertEqual(self.show()[1].splitlines(), [loaded, f"now: branch preflight/fix-1, commit {fixed[:7]}"])

    def test_show_without_target_json_reads_the_repository(self) -> None:
        repo = make_repo(self.dest, {"README.md": "# x\n"})
        git(repo, "remote", "add", "origin", LEAKED)
        sha = git(repo, "rev-parse", "HEAD")
        code, out, _ = self.show()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], f"TARGET: {GITHUB} (branch main, commit {sha[:7]}) -> {self.dest}/")
        self.assertNotIn("hunter2", out)

    def test_show_with_nothing_loaded_exits_1(self) -> None:
        self.assertEqual(self.show(), (1, NOTHING, ""))
        self.dest.mkdir()  # a folder, but no git repository in it
        code, out, _ = self.show()
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("TARGET: none ("), out)

    def test_show_through_the_real_entry_point(self) -> None:
        done = run_module("target", "--show", "--into", str(self.dest))
        self.assertEqual((done.returncode, done.stdout), (1, NOTHING))


class ExitCodeTests(TargetCase):
    def test_usage_errors_exit_2(self) -> None:
        for argv, words in (
            ((), "give SOURCE"),
            (("sample", "--show"), "--show takes no SOURCE"),
            (("--show", "--branch", "main"), "--show takes no SOURCE"),
        ):
            code, out, err = self.target(*argv)
            self.assertEqual((code, out), (2, ""), argv)
            self.assertIn(words, err)
        self.assertEqual(run_cli("target", "--bogus")[0], 2)  # argparse

    def test_into_never_moves_the_current_folder_or_the_checker(self) -> None:
        work = self.tmp / "work"
        (work / "inner").mkdir(parents=True)
        old_cwd = os.getcwd()
        os.chdir(work / "inner")
        self.addCleanup(os.chdir, old_cwd)
        for into in (".", "..", str(work), ""):
            code, _, err = run_cli("target", "sample", "--into", into)
            self.assertEqual(code, 2, into)
            self.assertIn("would move the current folder away", err)
        self.assertTrue((work / "inner").is_dir())

        kit = self.tmp / "kit"
        (kit / "preflight").mkdir(parents=True)
        with mock.patch.object(target, "ROOT", kit):
            code, _, err = run_cli("target", "sample", "--into", str(kit))
        self.assertEqual(code, 2)
        self.assertIn("would move the checker itself away", err)
        self.assertTrue((kit / "preflight").is_dir())


if __name__ == "__main__":
    unittest.main()
