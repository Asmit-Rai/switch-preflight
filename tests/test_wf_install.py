"""wf init and wf doctor (plan Task 3), and the installed guards working through real git."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, commit_all, git, git_env, make_repo, write
from wf import cli
from wf.cmd_build import BUILD_DIRS

PACKAGE = json.dumps({"name": "app", "scripts": {"test": "vitest run", "lint": "eslint .", "typecheck": "tsc --noEmit"}})
OLD_SETTINGS = {
    "permissions": {"allow": ["Bash(make:*)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": 'python3 "/old/kit/hooks/risky_git.py" --deny'},
            {"type": "command", "command": "echo another hook"},
        ]}],
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "echo after"}]}],
    },
}


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        env = git_env()
        env.update(WF_NOW="2026-11-05T10:00:00+05:30", WF_TMPDIR=str(self.tmp / "clones"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.project = make_repo(self.tmp / "app", {"README.md": "# app\n", "package.json": PACKAGE, "package-lock.json": "{}\n"})

    def init(self, *extra: str, code: int = 0) -> str:
        got, text = cli.run(["init", str(self.project), *extra], project=self.tmp)
        self.assertEqual(got, code, text)
        return text

    def wf(self, *argv: str, code: int = 0, stdin: str | None = None) -> str:
        got, text = cli.run(list(argv), project=self.project, stdin=stdin)
        self.assertEqual(got, code, text)
        return text

    def snapshot(self) -> dict[str, bytes]:
        paths = [".workflow/project.toml", "wf", ".claude/settings.json", ".git/info/exclude",
                 ".git/hooks/pre-commit", ".git/hooks/pre-merge-commit", ".git/hooks/pre-push"]
        return {p: (self.project / p).read_bytes() for p in paths if (self.project / p).exists()}


class InitTests(InstallCase):
    def test_init_writes_the_project_settings_from_the_project(self) -> None:
        self.init()
        data = tomllib.loads((self.project / ".workflow" / "project.toml").read_text())
        self.assertEqual(data["kit"], str(ROOT))
        self.assertEqual(data["main"], "main")
        self.assertEqual(data["install"], "npm ci")
        self.assertEqual(data["suite"], ["npm test", "npm run lint", "npm run typecheck"])
        self.assertEqual(data["max_loops"], 3)

    def test_options_win_and_an_existing_file_is_kept(self) -> None:
        self.init("--install", "true", "--suite", "true", "--suite", "echo ok", "--ports", "4321,4399", "--rules", "rules/x.toml")
        data = tomllib.loads((self.project / ".workflow" / "project.toml").read_text())
        self.assertEqual((data["install"], data["suite"], data["ports"], data["rules"]),
                         ("true", ["true", "echo ok"], [4321, 4399], "rules/x.toml"))
        self.assertIn("kept .workflow/project.toml", self.init("--install", "npm ci"))
        self.assertEqual(tomllib.loads((self.project / ".workflow" / "project.toml").read_text())["install"], "true")

    def test_the_launcher_runs_wf_for_this_folder(self) -> None:
        self.init()
        launcher = self.project / "wf"
        self.assertTrue(os.access(launcher, os.X_OK))
        proc = subprocess.run([str(launcher), "status"], cwd=str(self.tmp), capture_output=True, text=True, timeout=60)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "No task yet. Tell the manager what you want built."), proc.stderr)

    def test_git_hooks_and_exclude_entries_are_written_and_nothing_shows_in_git_status(self) -> None:
        self.init()
        for name in ("pre-commit", "pre-merge-commit", "pre-push"):
            hook = self.project / ".git" / "hooks" / name
            self.assertTrue(os.access(hook, os.X_OK), name)
            self.assertIn("# wf git hook", hook.read_text())
            self.assertIn(str(ROOT / "wf" / "githooks.py"), hook.read_text())
        exclude = (self.project / ".git" / "info" / "exclude").read_text().splitlines()
        for line in ("/wf", "/.workflow/", "/.claude/settings.json", "/.claude/settings.json.bak", ".DS_Store",
                     ".playwright-mcp/"):
            self.assertIn(line, exclude)
        self.assertEqual(git(self.project, "status", "--porcelain"), "")

    def test_the_browser_tool_folder_does_not_make_the_tree_unclean(self) -> None:
        # Found in the first live run: the Playwright browser tool writes .playwright-mcp/ into the project, the
        # approval hook refuses `rm -rf`, and an untracked folder stops ./wf build done.
        self.init()
        write(self.project, ".playwright-mcp/console-1.log", "[ERROR] favicon\n")
        write(self.project, ".playwright-mcp/page-1.yml", "- heading\n")
        write(self.project, "sub/.playwright-mcp/console-2.log", "[ERROR] favicon\n")  # any folder, on purpose
        self.assertEqual(git(self.project, "status", "--porcelain"), "")

    def test_running_init_again_adds_the_browser_tool_folder_to_an_older_exclude_file(self) -> None:
        self.init()
        exclude = self.project / ".git" / "info" / "exclude"
        exclude.write_text("".join(f"{line}\n" for line in exclude.read_text().splitlines() if line != ".playwright-mcp/"))
        self.assertNotIn(".playwright-mcp/", exclude.read_text().splitlines())
        self.init()
        self.assertIn(".playwright-mcp/", exclude.read_text().splitlines())
        settled = exclude.read_text()
        self.init()
        self.assertEqual(exclude.read_text(), settled, "a third run changes nothing")

    def test_settings_are_merged_and_the_old_hook_is_replaced(self) -> None:
        write(self.project, ".claude/settings.json", json.dumps(OLD_SETTINGS, indent=2))
        original = (self.project / ".claude" / "settings.json").read_text()
        self.init()
        data = json.loads((self.project / ".claude" / "settings.json").read_text())
        allow = data["permissions"]["allow"]
        for rule in ("Bash(make:*)", "Bash(./wf:*)", "Bash(git status:*)", "Bash(git clone:*)", "Bash(npx:*)", "Bash(kill:*)"):
            self.assertIn(rule, allow)
        for word in ("git add", "git commit", "git switch", "git push", "rm "):
            self.assertFalse([rule for rule in allow if word in rule], word)
        self.assertLessEqual({"/tmp", "/private/tmp"}, set(data["permissions"]["additionalDirectories"]))
        commands = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertEqual([c for c in commands if "risky_git.py" in c], [f'"{sys.executable}" "{ROOT}/hooks/risky_git.py" --policy wf'])
        self.assertIn("echo another hook", commands)
        self.assertEqual(data["hooks"]["PostToolUse"], OLD_SETTINGS["hooks"]["PostToolUse"])
        self.assertEqual((self.project / ".claude" / "settings.json.bak").read_text(), original)
        self.assertEqual(data["env"], {"BASH_DEFAULT_TIMEOUT_MS": "660000", "BASH_MAX_TIMEOUT_MS": "660000",
                                       "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1"})

    def test_a_second_init_changes_nothing(self) -> None:
        write(self.project, ".claude/settings.json", json.dumps(OLD_SETTINGS))
        self.init()
        before = self.snapshot()
        self.init()
        self.assertEqual(self.snapshot(), before)

    def test_init_refuses_a_folder_that_is_not_a_repository(self) -> None:
        folder = self.tmp / "plain"
        folder.mkdir()
        got, text = cli.run(["init", str(folder)], project=self.tmp)
        self.assertEqual(got, 1)
        self.assertIn("not a git repository", text)

    def test_the_build_dirs_match_the_git_hook(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("wf_githooks_check", ROOT / "wf" / "githooks.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(tuple(module.BUILD_DIRS), BUILD_DIRS)


class GuardsThroughGitTests(InstallCase):
    """The installed hooks let wf's own commits, merge and push through, and stop the raw ones."""

    def flow_to_ship(self) -> dict:
        self.init("--install", "true", "--suite", "true")
        self.wf("new", "add the file", "--by", "asmit")
        self.wf("spec", "submit", stdin="1. The file exists [unit]\nAssumed: nothing\n")
        self.wf("plan", "submit", stdin="1. file | items 1 | unit | tests/1.sh | sh tests/1.sh\n")
        self.wf("build", "start")
        write(self.project, "tests/1.sh", "test -f src/1.txt\n")
        self.wf("run", "--step", "1", "--red")
        self.wf("commit", "--step", "1", "--test", "-m", "the file exists", "--", "tests/1.sh")
        write(self.project, "src/1.txt", "x\n")
        self.wf("run", "--step", "1", "--green")
        self.wf("commit", "--step", "1", "-m", "add the file", "--", "src/1.txt")
        self.wf("build", "done")
        self.wf("review", "approve", "--findings", "clean")
        self.wf("test", "start")
        self.wf("test", "item", "1", "PASS", "--evidence", "sh tests/1.sh: exit 0")
        self.wf("test", "done")
        state = json.loads((self.project / ".workflow" / "state.json").read_text())
        return state["tasks"]["T1"]

    def test_the_local_merge_goes_through_the_hooks_and_a_raw_commit_on_main_does_not(self) -> None:
        task = self.flow_to_ship()
        self.wf("ship")
        self.assertEqual(git(self.project, "rev-parse", "main^2"), task["head"])
        write(self.project, "raw.txt", "x")
        git(self.project, "add", "raw.txt")
        proc = subprocess.run(["git", "-C", str(self.project), "commit", "-q", "-m", "raw"], capture_output=True, text=True, env=git_env())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no commits on main", proc.stderr)

    def test_ship_pushes_with_the_token_and_a_raw_push_is_refused(self) -> None:
        self.init("--install", "true", "--suite", "true")
        git(self.tmp, "init", "-q", "--bare", "remote.git")
        git(self.project, "remote", "add", "origin", str(self.tmp / "remote.git"))
        proc = subprocess.run(["git", "-C", str(self.project), "push", "-q", "origin", "main"], capture_output=True, text=True, env=git_env())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("only ./wf ship pushes", proc.stderr)
        subprocess.run(["git", "-C", str(self.project), "push", "-q", "--no-verify", "origin", "main"], check=True, env=git_env())
        task = self.flow_to_ship()
        self.wf("ship")
        self.assertEqual(git(self.tmp / "remote.git", "rev-parse", task["branch"]), task["head"])


class DoctorTests(InstallCase):
    def ready(self) -> None:
        self.init("--install", "true", "--suite", "true")

    def test_doctor_says_ready_after_init(self) -> None:
        self.ready()
        text = self.wf("doctor")
        self.assertTrue(text.endswith("Ready. Tell me what you want built."), text)

    def test_doctor_notes_a_project_set_up_before_the_browser_tool_folder_was_excluded(self) -> None:
        self.ready()
        exclude = self.project / ".git" / "info" / "exclude"
        exclude.write_text("".join(f"{line}\n" for line in exclude.read_text().splitlines() if line != ".playwright-mcp/"))
        text = self.wf("doctor")
        self.assertIn("Note: .playwright-mcp/ is not in .git/info/exclude", text)
        self.assertTrue(text.endswith("Ready. Tell me what you want built."), text)

    def test_doctor_names_each_problem_with_a_fix(self) -> None:
        self.ready()
        cases = {
            "pre-push": lambda: (self.project / ".git" / "hooks" / "pre-push").unlink(),
            "--policy wf": lambda: (self.project / ".claude" / "settings.json").write_text(
                (self.project / ".claude" / "settings.json").read_text().replace("--policy wf", "--deny")),
            "suite": lambda: (self.project / ".workflow" / "project.toml").write_text(
                (self.project / ".workflow" / "project.toml").read_text().replace('suite = ["true"]', "suite = []")),
            "not clean": lambda: write(self.project, "loose.txt", "x"),
        }
        good = self.snapshot()
        for words, break_it in cases.items():
            with self.subTest(problem=words):
                for rel, data in good.items():  # put every file back as init wrote it
                    (self.project / rel).write_bytes(data)
                    if rel == "wf" or rel.startswith(".git/hooks/"):
                        (self.project / rel).chmod(0o755)
                (self.project / "loose.txt").unlink(missing_ok=True)
                self.assertTrue(self.wf("doctor").endswith("Ready. Tell me what you want built."))
                break_it()
                text = self.wf("doctor", code=1)
                self.assertTrue(text.startswith("Not ready:"), text)
                self.assertIn(words, text)
                self.assertTrue(text.endswith("Fix these, then run ./wf doctor again."), text)
        (self.project / "loose.txt").unlink(missing_ok=True)

    def test_a_stopped_wf_stops_the_step_it_runs(self) -> None:  # found by the review
        self.ready()
        (self.project / ".workflow" / "project.toml").write_text(
            (self.project / ".workflow" / "project.toml").read_text().replace('suite = ["true"]', 'suite = ["sleep 31.7"]'))
        self.wf("new", "add it", "--by", "asmit")
        self.wf("spec", "submit", stdin="1. it is there [unit]\nAssumed: nothing\n")
        self.wf("plan", "submit", stdin="1. it | items 1 | unit | t.sh | sh t.sh\n")
        self.wf("build", "start")
        proc = subprocess.Popen([str(self.project / "wf"), "run", "--suite"], cwd=str(self.project),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and subprocess.run(["pgrep", "-f", "sleep 31.7"], capture_output=True).returncode:
            time.sleep(0.2)
        proc.terminate()
        out, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 130, out)
        time.sleep(0.5)
        self.assertNotEqual(subprocess.run(["pgrep", "-f", "sleep 31.7"], capture_output=True).returncode, 0,
                            "the suite's process outlived wf")
        self.assertIsNone(json.loads((self.project / ".workflow" / "state.json").read_text())["tasks"]["T1"]["build"]["suite"])

    def test_a_submit_with_nothing_piped_in_is_refused_quickly(self) -> None:  # found by the advisor
        self.ready()
        self.wf("new", "add it", "--by", "asmit")
        proc = subprocess.Popen([str(self.project / "wf"), "spec", "submit"], cwd=str(self.project), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        start = time.monotonic()
        try:
            out, _ = proc.communicate(timeout=20)  # stdin stays open: nobody writes to it, nobody closes it
        finally:
            if proc.stdin:
                proc.stdin.close()
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("no items", out)

    def test_doctor_without_init_says_how_to_set_up(self) -> None:
        text = self.wf("doctor", code=1)
        self.assertIn("python3 -m wf init", text)


if __name__ == "__main__":
    unittest.main()
