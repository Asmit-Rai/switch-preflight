"""`preflight doctor`: is this machine and the agents' shared folder ready? claude, gh and Switch are faked."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `python3 -m unittest tests.test_doctor` finds helpers

from helpers import EXAMPLES, ROOT, RULES, RepoCase, commit_all, make_repo, no_network, run_cli, write  # noqa: E402
from preflight import cli, doctor, net  # noqa: E402
from preflight.model import FAIL, PASS, SKIP, WARN  # noqa: E402

FAKE_BIN = "/fake/bin"
GH_SAYS = "github.com\n  Logged in to github.com account octo-hidden-account (keyring)\n  - Token scopes: 'gist', 'repo'\n"
SWITCH = "http://127.0.0.1:52258"
NAMES = ["python", "git", "claude", "gh", "layout", "kit", "settings", "hook", "rules", "switch"]


def fake_which(*missing: str) -> Callable[[str], str | None]:
    """claude and gh are pretend programs, python3 is the Python running the tests, git is the real one."""

    def which(name: str) -> str | None:
        if name in missing:
            return None
        if name in ("claude", "gh"):
            return f"{FAKE_BIN}/{name}"
        if name == "python3":
            return sys.executable
        return shutil.which(name)

    return which


def fake_run(gh_exit: int = 0, calls: list[list[str]] | None = None) -> doctor.Runner:
    """Answers for the pretend claude and gh; every other program (git, the hook) really runs."""

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(list(argv))
        if argv[0] == f"{FAKE_BIN}/claude":
            return subprocess.CompletedProcess(argv, 0, "2.1.31 (Claude Code)\n", "")
        if argv[0] == f"{FAKE_BIN}/gh":
            return subprocess.CompletedProcess(argv, gh_exit, GH_SAYS, GH_SAYS)
        return doctor.run_program(argv, **kwargs)

    return run


def machine(
    *missing: str,
    gh_exit: int = 0,
    fetch: net.Fetch = no_network,
    env: dict[str, str] | None = None,
    python: tuple[int, ...] = (3, 14, 2),
    calls: list[list[str]] | None = None,
) -> doctor.Machine:
    """A machine with the pretend tools. By default: no Switch URL, and any network call is an error."""
    return doctor.Machine(
        which=fake_which(*missing), run=fake_run(gh_exit, calls), fetch=fetch, env=env if env is not None else {}, python=python,
    )


def health(status: int = 200, seen: list[str] | None = None) -> net.Fetch:
    """A pretend Switch server: answers `status` and remembers every URL it was asked for."""

    def fetch(url: str, **kwargs: Any) -> net.Response:
        if seen is not None:
            seen.append(url)
        return net.Response(status, url)

    return fetch


def unreachable(url: str, **kwargs: Any) -> net.Response:
    raise net.NetError("connection refused")


def shared_folder(root: Path, name: str = "shared") -> Path:
    """A folder like scripts/setup-demo.sh makes: kit/ (a git clone with pf and the approval hook),
    rules/approved.toml, .claude/settings.json and a git project/."""
    folder = root / name
    kit = make_repo(folder / "kit")
    shutil.copy2(ROOT / "pf", kit / "pf")
    (kit / "hooks").mkdir()
    shutil.copy2(ROOT / "hooks" / "risky_git.py", kit / "hooks" / "risky_git.py")
    commit_all(kit, "checker")
    (folder / "rules").mkdir()
    shutil.copy2(RULES / "flip-the-switch.toml", folder / "rules" / "approved.toml")
    (folder / ".claude").mkdir()
    shutil.copy2(EXAMPLES / "claude-settings.json", folder / ".claude" / "settings.json")
    make_repo(folder / "project", {"README.md": "# sample\n"})
    return folder


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """Every file and folder under `folder` (inside .git too) with its size and modification time."""
    return {str(p.relative_to(folder)): (p.lstat().st_size, p.lstat().st_mtime_ns) for p in sorted(folder.rglob("*"))}


class DoctorCase(RepoCase):
    template: Path

    @classmethod
    def setUpClass(cls) -> None:
        # Building the git repos is the slow part: build one folder per class, then copy it for each test.
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.template = shared_folder(Path(tmp.name).resolve())

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.tmp / "shared"
        shutil.copytree(self.template, self.folder, symlinks=True)

    def doctor(self, *argv: str, machine: doctor.Machine, folder: Path | None = None) -> tuple[int, str]:
        """Run `doctor --folder <shared folder> ARGV` through the real parser. Returns (exit code, stdout)."""
        args = cli.build_parser().parse_args(["doctor", "--folder", str(folder or self.folder), *argv])
        out = io.StringIO()
        with redirect_stdout(out):
            code = doctor.cmd_doctor(args, machine)
        return code, out.getvalue()

    def findings(self, machine: doctor.Machine, switch_url: str | None = None) -> dict[str, doctor.Finding]:
        return {f.name: f for f in doctor.diagnose(self.folder, machine, switch_url)}

    def assertFinding(self, finding: doctor.Finding, status: str, *words: str) -> None:
        self.assertEqual(finding.status, status, f"{finding.name}: {finding.detail}")
        for word in words:
            self.assertIn(word, f"{finding.detail} | {finding.fix}")


class ReadyFolderTests(DoctorCase):
    def test_a_ready_folder_is_ok_and_exits_0(self) -> None:
        seen: list[str] = []
        calls: list[list[str]] = []
        code, out = self.doctor("--switch-url", SWITCH, machine=machine(fetch=health(200, seen), calls=calls))
        self.assertEqual(code, 0, out)
        lines = out.strip().splitlines()
        self.assertEqual(lines[-1], "DOCTOR: OK")
        self.assertTrue(lines[0].startswith("PREFLIGHT DOCTOR  folder: "))
        self.assertTrue(all(len(line) <= 100 for line in lines), out)
        self.assertNotIn("fix:", out)
        self.assertEqual(seen, [f"{SWITCH}/health"])

        findings = doctor.diagnose(self.folder, machine(fetch=health(200)), SWITCH)
        self.assertEqual([f.name for f in findings], NAMES)
        self.assertEqual({f.status for f in findings}, {PASS}, [f for f in findings if f.status != PASS])
        by_name = {f.name: f for f in findings}
        self.assertEqual(by_name["python"].detail, "3.14.2")
        self.assertEqual(by_name["claude"].detail, "2.1.31 (Claude Code)")
        self.assertIn("asks before git -C project reset --hard HEAD~1", by_name["hook"].detail)
        _, sha, _ = run_cli("sha", "--rules", str(self.folder / "rules" / "approved.toml"))
        self.assertEqual(by_name["rules"].detail, f"Flip the Switch Hackathon v1, 14 checks, sha256:{sha.strip()}")

        # programs start without a shell, from a full path; gh is asked only for its exit code
        self.assertTrue(all(isinstance(argv, list) and os.path.isabs(argv[0]) for argv in calls), calls)
        self.assertIn([f"{FAKE_BIN}/gh", "auth", "status"], calls)
        self.assertIn([f"{FAKE_BIN}/claude", "--version"], calls)
        self.assertNotIn("octo-hidden-account", out)
        self.assertNotIn("scopes", out)

    def test_without_a_switch_url_the_switch_check_is_a_skip_with_a_hint(self) -> None:
        code, out = self.doctor(machine=machine())  # no flag, no SWITCH_API_URL, and the network is off limits
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "DOCTOR: OK")
        self.assertFinding(self.findings(machine())["switch"], SKIP, "pass --switch-url http://127.0.0.1:<api port>")

    def test_the_switch_url_can_come_from_the_environment(self) -> None:
        seen: list[str] = []
        found = self.findings(machine(fetch=health(200, seen), env={"SWITCH_API_URL": "http://127.0.0.1:4000/"}))
        self.assertFinding(found["switch"], PASS, "http://127.0.0.1:4000/health answered 200")
        self.assertEqual(seen, ["http://127.0.0.1:4000/health"])

    def test_the_doctor_changes_nothing_in_the_folder(self) -> None:
        before = snapshot(self.folder)
        self.doctor("--switch-url", SWITCH, machine=machine(fetch=health(200)))
        self.doctor("--format", "chat", machine=machine())
        self.assertEqual(snapshot(self.folder), before)


class ProblemTests(DoctorCase):
    def test_missing_settings_is_a_fail_and_exits_1(self) -> None:
        (self.folder / ".claude" / "settings.json").unlink()
        code, out = self.doctor(machine=machine())
        self.assertEqual(code, 1, out)
        self.assertIn(".claude/settings.json is missing", out)
        self.assertIn("-> fix: a person runs scripts/setup-demo.sh; agents never edit this file", out)
        self.assertEqual(out.strip().splitlines()[-1], "DOCTOR: 1 problem(s), 0 warning(s)")

    def test_settings_must_allow_pf_and_register_the_hook_for_bash(self) -> None:
        good = (EXAMPLES / "claude-settings.json").read_text(encoding="utf-8")
        hook = '{"type": "command", "command": "python3 \\"$CLAUDE_PROJECT_DIR/kit/hooks/risky_git.py\\""}'
        cases = {
            '{"permissions": {"allow": ["Bash(kit/pf:*)"]}}': "no approval hook",
            '{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [%s]}]}}' % hook: "Bash(kit/pf:*) is not allowed",
            '{"permissions": {"allow": ["Bash(kit/pf:*)"]}, "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [%s]}]}}' % hook:
                "no approval hook",
            good.replace('"hooks": {', '"disableAllHooks": true, "hooks": {'): "disableAllHooks turns every hook off",
            '{"permissions": ': "is not valid JSON (line 1)",
            "[]": "is not a JSON object",
        }
        quick = machine("git", "python3")  # this check reads one file only: start no programs, so the test stays fast
        for text, words in cases.items():
            with self.subTest(text=text):
                write(self.folder, ".claude/settings.json", text)
                self.assertFinding(self.findings(quick)["settings"], FAIL, words, "a person runs scripts/setup-demo.sh")

        # a matcher that covers every tool counts; so does any regex that matches Bash
        for matcher in ("", "*", "Bash|Write"):
            with self.subTest(matcher=matcher):
                write(self.folder, ".claude/settings.json", good.replace('"matcher": "Bash"', f'"matcher": "{matcher}"'))
                self.assertFinding(self.findings(quick)["settings"], PASS)

        # an agent cannot switch the hook off from the local settings file either
        write(self.folder, ".claude/settings.json", good)
        write(self.folder, ".claude/settings.local.json", '{"disableAllHooks": true}')
        self.assertFinding(
            self.findings(quick)["settings"], FAIL, "disableAllHooks in .claude/settings.local.json",
            "a person deletes disableAllHooks from .claude/settings.local.json",
        )

    def test_a_tampered_hook_that_prints_nothing_fails(self) -> None:
        write(self.folder, "kit/hooks/risky_git.py", "import sys\nsys.stdin.read()\n")
        code, out = self.doctor(machine=machine())
        self.assertEqual(code, 1, out)
        found = self.findings(machine())
        self.assertFinding(found["hook"], FAIL, "did not ask before git -C project reset --hard HEAD~1", "git -C kit checkout -- hooks")
        self.assertFinding(found["kit"], WARN, "1 local change(s)")
        self.assertEqual(out.strip().splitlines()[-1], "DOCTOR: 1 problem(s), 1 warning(s)")

    def test_a_hook_that_asks_about_everything_crashes_or_hangs_fails(self) -> None:
        asks = 'import json, sys\nsys.stdin.read()\nprint(json.dumps({"hookSpecificOutput": {"permissionDecision": "ask"}}))\n'
        write(self.folder, "kit/hooks/risky_git.py", asks)
        self.assertFinding(self.findings(machine())["hook"], FAIL, "did not stay silent on git status")

        write(self.folder, "kit/hooks/risky_git.py", "raise SystemExit(3)\n")
        self.assertFinding(self.findings(machine())["hook"], FAIL, "(exit 3)")

        def hangs(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if argv[0] == sys.executable:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return fake_run()(argv, **kwargs)

        slow = machine()
        slow.run = hangs
        self.assertFinding(self.findings(slow)["hook"], FAIL, "no answer within 10 s")

        (self.folder / "kit" / "hooks" / "risky_git.py").unlink()
        self.assertFinding(self.findings(machine())["hook"], FAIL, "kit/hooks/risky_git.py is missing")

    def test_claude_missing_is_a_warning_and_exits_0(self) -> None:
        code, out = self.doctor(machine=machine("claude"))
        self.assertEqual(code, 0, out)
        self.assertIn("agents on this machine cannot start", out)
        self.assertEqual(out.strip().splitlines()[-1], "DOCTOR: 0 problem(s), 1 warning(s)")
        self.assertFinding(self.findings(machine("claude"))["claude"], WARN, "install Claude Code")

    def test_gh_missing_or_signed_out_is_a_warning_and_its_output_is_never_shown(self) -> None:
        code, out = self.doctor("--format", "chat", machine=machine(gh_exit=1))
        self.assertEqual(code, 0, out)
        self.assertIn("WARN gh: not signed in to GitHub", out)
        self.assertIn("gh auth login", out)
        self.assertNotIn("octo-hidden-account", out)
        self.assertNotIn("scopes", out)
        self.assertFinding(self.findings(machine("gh"))["gh"], WARN, "not installed", "only needed for private repos and pull requests")

    def test_an_unreachable_or_unhealthy_switch_fails(self) -> None:
        code, out = self.doctor("--switch-url", SWITCH, machine=machine(fetch=unreachable))
        self.assertEqual(code, 1, out)
        self.assertIn(f"{SWITCH}/health did not answer (connection refused)", out)
        self.assertIn("-> fix: open Switch Console and start the local server", out)

        self.assertFinding(self.findings(machine(fetch=health(503)), SWITCH)["switch"], FAIL, "answered HTTP 503")
        # a bad environment value fails without being printed
        found = self.findings(machine(env={"SWITCH_API_URL": "not-a-url-but-maybe-private"}))["switch"]
        self.assertFinding(found, FAIL, "SWITCH_API_URL is not an http(s) URL")
        self.assertNotIn("maybe-private", found.detail)

    def test_missing_approved_rules_is_a_warning_and_broken_rules_fail(self) -> None:
        approved = self.folder / "rules" / "approved.toml"
        approved.unlink()
        code, out = self.doctor(machine=machine())
        self.assertEqual(code, 0, out)
        self.assertIn("-> fix: @scout drafts it and the Captain approves it", out)
        self.assertFinding(self.findings(machine())["rules"], WARN, "rules/approved.toml is missing")

        write(self.folder, "rules/approved.toml", '[meta]\nname = "x"\nversion = 1\n[[check]]\nid = "a"\ntitle = "A"\ntype = "bogus"\n')
        self.assertFinding(self.findings(machine())["rules"], FAIL, "1 error(s)", "unknown check type 'bogus'", "kit/pf lint")

    def test_layout_needs_kit_pf_and_rules_and_wants_a_git_project(self) -> None:
        shutil.rmtree(self.folder / "project" / ".git")
        self.assertFinding(self.findings(machine())["layout"], WARN, "project/ is not a git repo", "run kit/pf target sample")

        (self.folder / "kit" / "pf").chmod(0o644)
        shutil.rmtree(self.folder / "rules")
        found = self.findings(machine())
        self.assertFinding(found["layout"], FAIL, "kit/pf is not executable", "rules/ is missing", "scripts/setup-demo.sh")
        self.assertFinding(found["rules"], WARN)

        shutil.rmtree(self.folder / "kit")
        found = self.findings(machine())
        self.assertFinding(found["layout"], FAIL, "kit/pf is missing")
        self.assertFinding(found["kit"], SKIP)
        self.assertFinding(found["hook"], FAIL, "kit/hooks/risky_git.py is missing")

    def test_old_python_or_missing_git_fail(self) -> None:
        self.assertFinding(self.findings(machine(python=(3, 10, 4)))["python"], FAIL, "3.10.4 is too old", "3.11 or newer")
        found = self.findings(machine("git"))
        self.assertFinding(found["git"], FAIL, "git is not on PATH", "xcode-select --install")
        self.assertFinding(found["kit"], SKIP, "git is not on PATH")
        self.assertFinding(found["layout"], PASS)

    def test_a_check_that_crashes_is_a_fail_and_the_others_still_run(self) -> None:
        def which(name: str) -> str | None:
            if name == "gh":
                raise RuntimeError("boom")
            return fake_which()(name)

        broken = machine()
        broken.which = which
        found = self.findings(broken)
        self.assertFinding(found["gh"], FAIL, "the check crashed (RuntimeError: boom)")
        self.assertEqual(list(found), NAMES)
        self.assertFinding(found["hook"], PASS)


class FormatTests(DoctorCase):
    def test_chat_lines_read_status_name_detail_and_fix(self) -> None:
        (self.folder / ".claude" / "settings.json").unlink()
        write(self.folder, "kit/hooks/risky_git.py", "import sys\nsys.stdin.read()\n")
        tools = machine("claude", gh_exit=1, fetch=unreachable)
        code, out = self.doctor("--format", "chat", "--switch-url", SWITCH, machine=tools)
        self.assertEqual(code, 1)
        lines = out.strip().splitlines()
        self.assertTrue(lines[0].startswith("PREFLIGHT DOCTOR  "))
        self.assertIn("PASS python: 3.14.2", lines)
        self.assertIn(
            "FAIL settings: .claude/settings.json is missing  -> fix: a person runs scripts/setup-demo.sh; agents never edit this file",
            lines,
        )
        self.assertIn(
            "FAIL hook: did not ask before git -C project reset --hard HEAD~1  "
            "-> fix: a person restores it: git -C kit checkout -- hooks; agents never edit it",
            lines,
        )
        self.assertNotIn("...", out)  # every line fits whole: nothing was cut
        self.assertEqual([line.split(" ", 2)[1].rstrip(":") for line in lines[1:-1]], NAMES)
        self.assertEqual(lines[-1], "DOCTOR: 3 problem(s), 3 warning(s)")

    def test_chat_stays_under_1800_characters_even_when_everything_is_wrong(self) -> None:
        folder = shared_folder(self.tmp, "a-very-long-shared-folder-name-" * 6)
        shutil.rmtree(folder / "rules")
        shutil.rmtree(folder / "project")
        (folder / ".claude" / "settings.json").unlink()
        write(folder, "kit/hooks/risky_git.py", "print('x' * 5000)\n")
        worst = machine("claude", "gh", python=(3, 9, 0), fetch=unreachable)
        code, out = self.doctor("--format", "chat", "--switch-url", SWITCH, machine=worst, folder=folder)
        self.assertEqual(code, 1)
        self.assertLessEqual(len(out.strip()), 1800, out)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 12)
        self.assertRegex(lines[-1], r"^DOCTOR: \d+ problem\(s\), \d+ warning\(s\)$")

        _, table = self.doctor("--switch-url", SWITCH, machine=worst, folder=folder)
        self.assertTrue(all(len(line) <= 100 for line in table.splitlines()), table)

        # however long the details are, and however many lines there are, the message stays in one piece
        many = [doctor.Finding(f"check{n}", FAIL, "d" * 500, "f" * 300) for n in range(30)]
        text = doctor.format_chat(many, folder)
        self.assertLessEqual(len(text), 1800)
        self.assertRegex(text, r"\n\(\+\d+ more, run with --format table\)\nDOCTOR: 30 problem\(s\), 0 warning\(s\)$")


class CommandLineTests(unittest.TestCase):
    def test_usage_errors_exit_2(self) -> None:
        code, _, err = run_cli("doctor", "--format", "html")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)
        code, _, err = run_cli("doctor", "--switch-url", "ftp://example.org")
        self.assertEqual(code, 2)
        self.assertIn("not an http(s) URL", err)
        code, _, err = run_cli("doctor", "--folder", str(ROOT / "no-such-folder"))
        self.assertEqual(code, 2)
        self.assertIn("folder not found", err)

    def test_the_folder_defaults_to_the_current_one(self) -> None:
        args = cli.build_parser().parse_args(["doctor"])
        self.assertEqual((args.folder, args.switch_url, args.format), (".", None, "table"))

    def test_programs_get_no_terminal_input_and_are_stopped_when_too_slow(self) -> None:
        echo = doctor.run_program([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=10)
        self.assertEqual(echo.stdout.strip(), "''")
        upper = doctor.run_program([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], stdin_text="hi")
        self.assertEqual(upper.stdout.strip(), "HI")
        with self.assertRaises(subprocess.TimeoutExpired):
            doctor.run_program([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)


if __name__ == "__main__":
    unittest.main()
