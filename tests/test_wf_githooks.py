"""The git hooks of a wf project (wf/githooks.py): pre-commit, pre-merge-commit and pre-push.

The module is loaded by file path, the way git runs it. wf/ never goes on sys.path, so wf's other
modules (state, cli ...) cannot turn into top-level names for the rest of the test run.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also when run as `python3 -m unittest tests.test_wf_githooks`
from helpers import ROOT, git, git_env, make_repo, write  # noqa: E402

SCRIPT = ROOT / "wf" / "githooks.py"
_spec = importlib.util.spec_from_file_location("wf_githooks_under_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
githooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(githooks)

# What `wf init` writes into .git/hooks/, one per hook.
SHIM = '#!/bin/sh\n# wf git hook: written by wf init. The rules live in the kit.\nexec "{python}" "{script}" {hook} "$@"\n'

SHA = "a" * 40
OTHER = "b" * 40
ZEROS = "0" * 40
NO_COMMIT_ON_MAIN = "wf: no commits on main. Work happens on the task branch (./wf build start); only ./wf ship merges into main."
NO_TOKEN = "wf: push refused: only ./wf ship pushes (there is no ship token)."
NO_DELETE = "wf: push refused: deleting a remote branch is not allowed."


def kept_out(path: str) -> str:
    return f"wf: refusing to commit {path}: node_modules, build output and .workflow/ never go into git."


def push_line(local_sha: str, remote_sha: str = ZEROS) -> str:
    """One stdin line of pre-push. A remote sha of zeros means a new branch."""
    return f"refs/heads/t1-add-x {local_sha} refs/heads/t1-add-x {remote_sha}"


class ProjectCase(unittest.TestCase):
    """A project on main with one commit. Its git, and githooks' own git calls, ignore the machine's git config."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.enterContext(mock.patch.dict(os.environ, git_env()))
        self.repo = make_repo(self.tmp / "project", {"README.md": "# app\n"})

    def stage(self, rel: str) -> None:
        write(self.repo, rel, "x\n")
        git(self.repo, "add", "-f", "--", rel)  # -f: the machine's own gitignore must not decide what is staged

    def branch(self, name: str = "t1-add-x") -> None:
        git(self.repo, "switch", "-q", "-c", name)


class MainBranchTests(unittest.TestCase):
    def test_main_unless_project_toml_names_another(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertEqual(githooks.main_branch(repo), "main")  # no file
            for text in ('install = "npm ci"\n', "main = [\n", "main = 5\n", 'main = ""\n', b"main = '\xff'\n"):
                write(repo, ".workflow/project.toml", text)
                self.assertEqual(githooks.main_branch(repo), "main", text)  # no key, not TOML, not a name
            write(repo, ".workflow/project.toml", 'kit = "/k"\nmain = "trunk"\n')
            self.assertEqual(githooks.main_branch(repo), "trunk")


class PreCommitTests(ProjectCase):
    def test_refuses_a_commit_on_main(self) -> None:
        self.stage("a.txt")
        for env in ({}, {"WF_SHIP": "0"}, {"WF_SHIP": "yes"}):
            self.assertEqual(githooks.pre_commit(self.repo, env), NO_COMMIT_ON_MAIN, env)

    def test_wf_ship_may_commit_on_main(self) -> None:
        self.stage("a.txt")
        self.assertIsNone(githooks.pre_commit(self.repo, {"WF_SHIP": "1"}))

    def test_allows_a_commit_on_a_task_branch(self) -> None:
        self.branch()
        self.stage("src/a.ts")
        self.assertIsNone(githooks.pre_commit(self.repo, {}))

    def test_honours_the_main_branch_named_in_project_toml(self) -> None:
        write(self.repo, ".workflow/project.toml", 'main = "trunk"\n')
        self.stage("a.txt")
        self.assertIsNone(githooks.pre_commit(self.repo, {}))  # main is an ordinary branch here
        self.branch("trunk")
        self.assertEqual(
            githooks.pre_commit(self.repo, {}),
            "wf: no commits on trunk. Work happens on the task branch (./wf build start); only ./wf ship merges into trunk.",
        )

    def test_refuses_node_modules_build_output_and_workflow_state(self) -> None:
        self.branch()
        for path in ("node_modules/x.js", "src/node_modules/y.js", "dist/app.js", ".workflow/state.json"):
            with self.subTest(path=path):
                self.stage(path)
                self.assertEqual(githooks.pre_commit(self.repo, {}), kept_out(path))
                self.assertEqual(githooks.pre_commit(self.repo, {"WF_SHIP": "1"}), kept_out(path))  # WF_SHIP lifts only the main rule
                git(self.repo, "rm", "-q", "--cached", "--", path)

    def test_allows_source_files_whose_names_only_look_like_build_output(self) -> None:
        self.branch()
        for path in ("src/a.ts", "src/dist/b.ts", "docs/build-notes.md", "node_modules.md"):
            self.stage(path)
        self.assertIsNone(githooks.pre_commit(self.repo, {}))

    def test_deleting_tracked_build_output_is_allowed(self) -> None:
        self.stage("dist/old.js")
        git(self.repo, "commit", "-q", "-m", "old build output")  # no hook is installed in this test
        self.branch()
        git(self.repo, "rm", "-q", "--", "dist/old.js")
        self.assertIsNone(githooks.pre_commit(self.repo, {}))


class PrePushTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)  # pre_push reads only .workflow/ship-token

    def give_token(self, sha: str) -> None:
        write(self.repo, ".workflow/ship-token", sha + "\n")

    def test_refuses_without_a_token(self) -> None:
        self.assertEqual(githooks.pre_push(self.repo, [push_line(SHA)]), NO_TOKEN)
        self.give_token("")
        self.assertEqual(githooks.pre_push(self.repo, [push_line(SHA)]), NO_TOKEN)  # an empty token names nothing

    def test_refuses_when_the_token_names_another_commit(self) -> None:
        self.give_token(SHA)
        self.assertEqual(
            githooks.pre_push(self.repo, [push_line(OTHER)]),
            f"wf: push refused: the ship token names {SHA[:7]}, not {OTHER[:7]}.",
        )

    def test_refuses_deleting_a_remote_branch(self) -> None:
        self.give_token(SHA)
        self.assertEqual(githooks.pre_push(self.repo, [f"(delete) {ZEROS} refs/heads/t1-add-x {SHA}"]), NO_DELETE)

    def test_allows_the_commit_the_token_names(self) -> None:
        self.give_token(SHA)
        self.assertIsNone(githooks.pre_push(self.repo, [push_line(SHA)]))  # a new branch: remote sha of zeros
        self.assertIsNone(githooks.pre_push(self.repo, [push_line(SHA, OTHER)]))  # an update

    def test_ignores_blank_lines(self) -> None:
        self.give_token(SHA)
        self.assertIsNone(githooks.pre_push(self.repo, ["", push_line(SHA), "   ", ""]))


class MainTests(unittest.TestCase):
    def test_an_unknown_hook_name_never_blocks_work(self) -> None:
        for argv in ([], ["post-commit"], ["pre_commit"], ["--help"]):
            self.assertEqual(githooks.main(argv), 0, argv)


class RealGitTests(ProjectCase):
    """Through real git, with the shims wf init installs (this Python, this kit's githooks.py)."""

    def setUp(self) -> None:
        super().setUp()
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        for hook in ("pre-commit", "pre-merge-commit", "pre-push"):
            shim = hooks / hook
            shim.write_text(SHIM.format(python=sys.executable, script=SCRIPT, hook=hook), encoding="utf-8")
            shim.chmod(0o755)

    def run_git(self, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        """git in the test project, where a refusal is an answer, not an error."""
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, env={**git_env(), **extra_env}, timeout=60, check=False,
        )

    def commit(self, rel: str, *flags: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        self.stage(rel)
        return self.run_git("commit", "-q", *flags, "-m", f"add {rel}", **extra_env)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def test_a_commit_on_main_is_refused(self) -> None:
        before = self.head()
        done = self.commit("a.txt")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(NO_COMMIT_ON_MAIN, done.stderr)
        self.assertEqual(self.head(), before)

    def test_wf_ship_commits_on_main(self) -> None:
        done = self.commit("a.txt", WF_SHIP="1")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_commit_on_a_task_branch_goes_through(self) -> None:
        self.branch()
        done = self.commit("src/a.ts")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_commit_all_is_checked_on_the_index_git_is_committing(self) -> None:
        # git commit -a builds its own index and names it in GIT_INDEX_FILE: the hook must read that one
        self.assertEqual(self.commit("dist/old.js", "--no-verify").returncode, 0)  # tracked build output, from before wf
        self.branch()
        write(self.repo, "dist/old.js", "changed\n")
        done = self.run_git("commit", "-q", "-a", "-m", "rebuild")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(kept_out("dist/old.js"), done.stderr)

    def test_a_merge_commit_on_main_needs_wf_ship(self) -> None:
        self.branch()
        self.assertEqual(self.commit("src/a.ts").returncode, 0)
        git(self.repo, "switch", "-q", "main")
        before = self.head()
        refused = self.run_git("merge", "--no-ff", "-m", "merge t1", "t1-add-x")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(NO_COMMIT_ON_MAIN, refused.stderr)
        self.assertEqual(self.head(), before)
        git(self.repo, "merge", "--abort")  # a refused merge stays pending, as with --no-commit
        merged = self.run_git("merge", "--no-ff", "-m", "merge t1", "t1-add-x", WF_SHIP="1")
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertEqual(len(git(self.repo, "rev-list", "--parents", "-n", "1", "HEAD").split()), 3)  # two parents

    def test_a_push_needs_the_ship_token_to_name_head(self) -> None:
        remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", remote.name)
        git(self.repo, "remote", "add", "origin", str(remote))
        self.branch()
        self.assertEqual(self.commit("src/a.ts").returncode, 0)
        head = self.head()

        refused = self.run_git("push", "-q", "origin", "t1-add-x")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(NO_TOKEN, refused.stderr)
        self.assertEqual(git(remote, "branch", "--list"), "")

        write(self.repo, ".workflow/ship-token", head + "\n")
        pushed = self.run_git("push", "-q", "origin", "t1-add-x")
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.assertEqual(git(remote, "rev-parse", "refs/heads/t1-add-x"), head)

        deleted = self.run_git("push", "-q", "origin", "--delete", "t1-add-x")
        self.assertNotEqual(deleted.returncode, 0)
        self.assertIn(NO_DELETE, deleted.stderr)
        self.assertEqual(git(remote, "rev-parse", "refs/heads/t1-add-x"), head)

    def test_a_module_beside_the_script_cannot_hide_a_standard_one(self) -> None:
        # Another wf module named like a standard one (wf/types.py) must not be imported when git runs the script.
        kit = self.tmp / "kit" / "wf"
        kit.mkdir(parents=True)
        (kit / "githooks.py").write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        (kit / "types.py").write_text("raise SystemExit('wf/types.py was imported')\n", encoding="utf-8")
        self.branch()
        done = subprocess.run(
            [sys.executable, str(kit / "githooks.py"), "pre-commit"],
            cwd=self.repo, capture_output=True, text=True, env=git_env(), timeout=60, check=False,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
