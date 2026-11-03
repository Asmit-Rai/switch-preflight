"""The sample project generator: deterministic, exactly 6 planted defects, and an all-green twin."""

from __future__ import annotations

import io
import subprocess
import sys
import time
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from helpers import EXAMPLES, FIXTURES, RULES, RepoCase, git, no_network, read_verdict, run_cli  # also puts examples/ on sys.path

import make_sample  # noqa: E402
from preflight import net  # noqa: E402
from preflight.checks import Context, run_checks  # noqa: E402
from preflight.checks.secrets import find_secrets  # noqa: E402
from preflight.ruleset import load_ruleset  # noqa: E402
from preflight.verdict import check_gate  # noqa: E402

CONTEST = RULES / "flip-the-switch.toml"
OFFLINE = str(FIXTURES / "offline-green.toml")
NETWORK_CHECKS = {"repo-public", "video-max-180s"}  # the contest checks that need the internet (video-link is text-only)
VIDEO_URL = "https://www.youtube.com/watch?v=mA4_v5fQDYE"
# The 6 planted defects turn exactly these 6 required checks red, one check each.
SIX_FAILING = ("readme-headings", "license", "links-ok", "secret-scan", "tests-pass", "video-link-present")
# Offline, every other check of the contest ruleset passes, except the two that need the internet.
QUIET = {"first-commit-after-start", "no-env-file", "git-clean", "template-present", "max-file-25mb", "no-todo-markers"}
COMMITS = (  # (author and committer date, subject), oldest first: all after the contest start
    ("2026-11-04T00:30:00+05:30", "Start the roll call script"),
    ("2026-11-04T00:40:00+05:30", "Add README, tests and the room template"),
    ("2026-11-04T00:50:00+05:30", "Add local settings"),
)
TRACKED = {".gitignore", "README.md", "app.py", "settings_local.py", "templates/room.yaml", "tests/test_roll_call.py"}
LATER = "2026-11-04T01:00:00+05:30"  # a commit made after the sample was generated


def tree_fingerprint(repo: Path) -> dict[str, Any]:
    """Everything about a repo that must be identical between two generations."""
    return {
        "log": git(repo, "log", "--format=%H|%an|%ae|%aI|%cI|%s"),
        "tree": git(repo, "ls-tree", "-r", "HEAD"),
        "origin": git(repo, "config", "--get", "remote.origin.url"),
        "branch": git(repo, "symbolic-ref", "--short", "HEAD"),
        "status": git(repo, "status", "--porcelain"),
    }


def quiet_main(argv: list[str]) -> int:
    """make_sample.main() without its console output."""
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return make_sample.main(argv)


def fake_internet(url: str, method: str = "GET", **kwargs: Any) -> net.Response:
    """Answers like a healthy internet: the made-up repo is public, the video is 95 s long."""
    if url.startswith("https://api.github.com/repos/example-team/sample-submission"):
        return net.Response(200, url, {}, b'{"private": false}')
    if url.startswith(VIDEO_URL):
        return net.Response(200, url, {}, b'<html>"lengthSeconds":"95"</html>')
    return net.Response(200, url)


class SampleCase(RepoCase):
    def sample(self, name: str, fixed: bool = False) -> Path:
        dest = self.tmp / name
        make_sample.create_sample(dest, fixed=fixed)
        return dest

    def results(self, project: Path, **ctx: Any) -> dict[str, Any]:
        rules = load_ruleset(CONTEST)
        return {r.id: r for r in run_checks(rules, Context(project, **ctx))}

    def run_tests(self, project: Path) -> subprocess.CompletedProcess[str]:
        """The contest ruleset's `tests-pass` command, run with the project as the working directory."""
        return subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=project, capture_output=True, text=True
        )


class DeterminismTests(SampleCase):
    def test_two_generations_are_identical(self) -> None:
        for fixed in (False, True):
            with self.subTest(fixed=fixed):
                a = self.sample(f"a{fixed}", fixed)
                b = self.sample(f"b{fixed}", fixed)
                self.assertEqual(tree_fingerprint(a), tree_fingerprint(b))
                self.assertEqual(tree_fingerprint(a)["status"], "")  # everything is committed
                files = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}
                self.assertEqual(files(a), files(b))

    def test_identity_and_dates_are_fixed(self) -> None:
        for fixed in (False, True):
            with self.subTest(fixed=fixed):
                repo = self.sample(f"dates{fixed}", fixed)
                log = git(repo, "log", "--reverse", "--format=%aI|%cI|%an <%ae>|%s").splitlines()
                self.assertEqual(log, [f"{when}|{when}|Sample Team <sample@example.com>|{subject}" for when, subject in COMMITS])
                self.assertEqual(git(repo, "config", "--get", "remote.origin.url"), "https://github.com/example-team/sample-submission.git")
                self.assertEqual(git(repo, "symbolic-ref", "--short", "HEAD"), "main")

    def test_destination_rules(self) -> None:
        existing = self.tmp / "occupied"
        existing.mkdir()
        (existing / "keep.txt").write_text("mine")
        self.assertEqual(quiet_main([str(existing)]), 2)
        self.assertEqual((existing / "keep.txt").read_text(), "mine")
        self.assertFalse((existing / ".git").exists())
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(quiet_main([str(empty)]), 0)
        self.assertTrue((empty / ".git").is_dir())
        afile = self.tmp / "afile"
        afile.write_text("x")
        self.assertEqual(quiet_main([str(afile)]), 2)

    def test_runs_as_a_script(self) -> None:
        dest = self.tmp / "viascript"
        proc = subprocess.run([sys.executable, str(EXAMPLES / "make_sample.py"), str(dest)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("6 planted defects", proc.stdout)
        self.assertEqual(proc.stdout.count("\n  "), 6)
        again = subprocess.run([sys.executable, str(EXAMPLES / "make_sample.py"), str(dest)], capture_output=True, text=True)
        self.assertEqual(again.returncode, 2)


class DefectTests(SampleCase):
    def test_exactly_six_defects_are_planted(self) -> None:
        self.assertEqual([number for number, _, _ in make_sample.DEFECTS], [1, 2, 3, 4, 5, 6])
        self.assertEqual([checks for _, _, checks in make_sample.DEFECTS], [(check,) for check in SIX_FAILING])  # one check each

    def test_the_broken_sample_fails_exactly_the_checks_of_those_six_defects(self) -> None:
        project = self.sample("broken")
        results = self.results(project, offline=True, fetch=no_network)  # offline: network checks SKIP
        failing = {i for i, r in results.items() if r.status == "FAIL"}
        self.assertEqual(failing, set(SIX_FAILING))
        self.assertTrue(all(results[i].required for i in failing))  # all six block the verdict
        for number, text, checks in make_sample.DEFECTS:
            for check in checks:
                self.assertEqual(results[check].status, "FAIL", f"defect {number} ({text}) should fail {check}")
        self.assertEqual({i for i, r in results.items() if r.status == "PASS"}, QUIET)
        self.assertEqual({i for i, r in results.items() if r.status == "SKIP"}, NETWORK_CHECKS)

    def test_defect_details(self) -> None:
        project = self.sample("broken")
        results = self.results(project, offline=True, fetch=no_network)
        settings = (project / "settings_local.py").read_text().splitlines()
        token_line = next(n for n, line in enumerate(settings, start=1) if "GITHUB_TOKEN" in line)
        self.assertIn("missing heading(s): Agents, Reused", results["readme-headings"].detail)
        self.assertIn("no file matching LICENSE", results["license"].detail)
        self.assertIn("docs.example.invalid/setup", results["links-ok"].detail)
        self.assertIn(f"settings_local.py:{token_line} GitHub token (ghp_****)", results["secret-scan"].detail)
        self.assertIn("exit 1", results["tests-pass"].detail)
        self.assertIn("no video link", results["video-link-present"].detail)
        self.assertIn("2026-11-03T19:00:00Z", results["first-commit-after-start"].detail)  # 00:30 IST, after the start
        self.assertEqual(results["no-env-file"].status, "PASS")  # settings_local.py is not a name that check forbids

    def test_the_planted_token_is_a_runtime_built_fake_that_the_scanner_masks(self) -> None:
        token = make_sample.fake_github_token()
        self.assertEqual(len(token), 40)
        self.assertEqual([h.pattern for h in find_secrets(f'GITHUB_TOKEN = "{token}"')], ["GitHub token"])
        source = (EXAMPLES / "make_sample.py").read_text()
        self.assertNotIn(token, source)
        self.assertEqual([line for line in source.splitlines() if find_secrets(line)], [])
        settings = (self.sample("broken") / "settings_local.py").read_text()
        self.assertIn(f'GITHUB_TOKEN = "{token}"', settings)  # it is in the generated repo only

    def test_the_token_is_only_in_the_last_commit(self) -> None:
        repo = self.sample("broken")
        token = make_sample.fake_github_token()
        self.assertEqual(git(repo, "log", "-1", "--format=%s"), "Add local settings")
        self.assertEqual(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"), "settings_local.py")
        self.assertIn(token, git(repo, "log", "-p", "-1"))
        self.assertNotIn(token, git(repo, "log", "-p", "HEAD~1"))  # nothing earlier holds it

    def test_the_failing_test_is_fixed_by_one_line_in_the_app_not_by_weakening_the_test(self) -> None:
        project = self.sample("broken")
        self.assertIn('"Ada, Grace, Linus"', (project / "tests" / "test_roll_call.py").read_text())  # the test asserts the right thing
        started = time.monotonic()
        broken = self.run_tests(project)
        self.assertLess(time.monotonic() - started, 5)  # fast: no network, no sleeping
        self.assertNotEqual(broken.returncode, 0)
        self.assertIn("FAILED (failures=1)", broken.stderr)
        app = project / "app.py"
        self.assertEqual(app.read_text().count('" ".join(names)'), 1)
        app.write_text(app.read_text().replace('" ".join(names)', '", ".join(names)'))
        fixed = self.run_tests(project)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertEqual(git(project, "diff", "--name-only"), "app.py")  # only the app changed, the test is untouched

    def test_the_sample_never_mentions_a_dot_env_file(self) -> None:
        for fixed in (False, True):
            with self.subTest(fixed=fixed):
                repo = self.sample(f"noenv{fixed}", fixed)
                tracked = git(repo, "ls-files").splitlines()
                self.assertEqual(set(tracked), TRACKED | ({"LICENSE"} if fixed else set()))
                everything = git(repo, "log", "--all", "-p") + "".join((repo / name).read_text() for name in tracked)
                self.assertNotIn(".env", everything)

    def test_the_broken_sample_is_not_ready_through_the_cli(self) -> None:
        project = self.sample("broken")
        code, out, _ = run_cli("run", "--project", str(project), "--rules", OFFLINE, "--format", "chat")
        self.assertEqual(code, 1)
        last = out.strip().splitlines()[-1]
        for check in ("readme-headings", "license", "secret-scan", "tests-pass"):  # the fixture's checks that the defects turn red
            self.assertIn(check, last)
        for check in ("no-env-file", "first-commit-after-start"):
            self.assertNotIn(check, last)
        self.assertEqual(run_cli("verdict", "--project", str(project))[0], 1)
        code, out, _ = run_cli("pack", "--project", str(project), "--out", str(self.tmp / "pack.md"))
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "pack.md").exists())

    def test_the_sample_ruleset_prints_six_fail_lines(self) -> None:
        project = self.sample("broken")
        args = ("run", "--project", str(project), "--rules", str(EXAMPLES / "sample.rules.toml"), "--offline", "--format", "chat", "--no-write")
        code, out, _ = run_cli(*args)
        self.assertEqual(code, 1)
        failing = [line.split(":")[0].removeprefix("FAIL ") for line in out.splitlines() if line.startswith("FAIL ")]
        self.assertEqual(sorted(failing), sorted(SIX_FAILING))


class PurgeTests(SampleCase):
    """The token is only in the last commit, so a person (with the Captain's approval) can purge it."""

    def purge(self, name: str, rewrite: Callable[[str], str]) -> Path:
        """`git reset --soft HEAD~1`, change settings_local.py, commit again."""
        project = self.sample(name)
        git(project, "reset", "--soft", "HEAD~1")  # drop "Add local settings"; its files stay staged
        settings = project / "settings_local.py"
        settings.write_text(rewrite(settings.read_text()))
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "Add local settings", when=LATER)
        return project

    def test_reset_soft_edit_and_commit_again_purges_the_token_from_history(self) -> None:
        token = make_sample.fake_github_token()
        ways: dict[str, Callable[[str], str]] = {
            "delete-the-line": lambda text: "".join(line for line in text.splitlines(keepends=True) if "GITHUB_TOKEN" not in line),
            "read-it-from-the-environment": lambda text: make_sample.settings_py(fixed=True),
        }
        for name, rewrite in ways.items():
            with self.subTest(name):
                project = self.purge(name, rewrite)
                results = self.results(project, offline=True, fetch=no_network)
                self.assertEqual(results["secret-scan"].status, "PASS", results["secret-scan"].detail)
                self.assertIn("3 commit(s)", results["secret-scan"].detail)  # the contest check scans history, and it is clean
                self.assertEqual({i for i, r in results.items() if r.status == "FAIL"}, set(SIX_FAILING) - {"secret-scan"})
                self.assertEqual(results["first-commit-after-start"].status, "PASS")
                self.assertEqual(git(project, "rev-list", "--count", "HEAD"), "3")
                self.assertNotIn(token, git(project, "log", "--all", "-p"))  # gone from every reachable commit

    def test_a_plain_follow_up_commit_leaves_the_token_in_history(self) -> None:
        project = self.sample("followup")
        (project / "settings_local.py").write_text(make_sample.settings_py(fixed=True))
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "Read the token from the environment", when=LATER)
        result = self.results(project, offline=True, fetch=no_network)["secret-scan"]
        self.assertEqual(result.status, "FAIL")  # the files are clean but the token is still in an old commit
        self.assertIn("in history", result.detail)


class FixedSampleTests(SampleCase):
    def test_the_fixed_sample_has_none_of_the_defects(self) -> None:
        project = self.sample("fixed", fixed=True)
        self.assertTrue((project / "LICENSE").is_file())
        readme = (project / "README.md").read_text()
        for heading in ("## Agents we connected", "## Reused vs built during the hackathon"):
            self.assertIn(heading, readme)
        self.assertIn(VIDEO_URL, readme)
        self.assertNotIn("invalid", readme)
        proc = self.run_tests(project)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        settings = (project / "settings_local.py").read_text()
        self.assertIn('environ.get("GITHUB_TOKEN"', settings)  # the file exists but reads its token from the environment
        self.assertEqual(find_secrets(settings), [])
        self.assertNotIn(make_sample.fake_github_token(), git(project, "log", "--all", "-p"))

    def test_the_fixed_sample_is_ready_under_the_real_contest_ruleset(self) -> None:
        project = self.sample("fixed", fixed=True)
        results = self.results(project, fetch=fake_internet)  # a healthy internet, no real network
        self.assertEqual({i: r.status for i, r in results.items() if r.status != "PASS"}, {})
        self.assertIn("video is 1:35", results["video-max-180s"].detail)
        self.assertIn("github.com/example-team/sample-submission is public", results["repo-public"].detail)

    def test_the_fixed_sample_is_not_ready_offline_because_network_checks_cannot_be_verified(self) -> None:
        results = self.results(self.sample("fixed", fixed=True), offline=True, fetch=no_network)
        self.assertEqual({i for i, r in results.items() if r.status != "PASS"}, NETWORK_CHECKS | {"links-ok"})
        self.assertEqual(results["links-ok"].status, "SKIP")

    def test_ready_then_the_gate_opens_and_the_pack_is_written(self) -> None:
        project = self.sample("fixed", fixed=True)
        code, out, _ = run_cli("run", "--project", str(project), "--rules", OFFLINE, "--format", "table")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "VERDICT: READY")
        head = git(project, "rev-parse", "HEAD")
        code, out, _ = run_cli("verdict", "--project", str(project))
        self.assertEqual((code, out.strip()), (0, f"GATE OK: ready at {head[:7]}"))
        pack = self.tmp / "SUBMISSION.md"
        code, out, _ = run_cli("pack", "--project", str(project), "--out", str(pack))
        self.assertEqual(code, 0, out)
        text = pack.read_text()
        self.assertIn("# Submission pack: Roll Call", text)
        self.assertIn("https://github.com/example-team/sample-submission", text)
        self.assertIn(VIDEO_URL, text)
        self.assertIn(head, text)
        self.assertEqual(read_verdict(project)["summary"], {"pass": 9, "fail": 0, "warn": 0, "skip": 0})

    def test_the_gate_closes_again_after_one_more_commit(self) -> None:
        project = self.sample("fixed", fixed=True)
        run_cli("run", "--project", str(project), "--rules", OFFLINE, "--format", "chat")
        self.assertTrue(check_gate(project).ok)
        (project / "app.py").write_text((project / "app.py").read_text() + "\n# tweak\n")
        self.assertFalse(check_gate(project).ok)
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "tweak", when="2026-11-04T12:00:00+05:30")
        self.assertIn("HEAD is", check_gate(project).message)


class SampleRulesetTests(SampleCase):
    def test_sample_ruleset_makes_the_fixed_sample_ready_without_the_repo_check(self) -> None:
        project = self.sample("fixed", fixed=True)
        rules = load_ruleset(EXAMPLES / "sample.rules.toml")
        results = {r.id: r for r in run_checks(rules, Context(project, fetch=lambda url, **kw: net.Response(404, url) if "api.github.com" in url else fake_internet(url)))}
        self.assertEqual(results["repo-public"].status, "WARN")  # the made-up repo is not public...
        self.assertEqual([i for i, r in results.items() if r.required and r.status != "PASS"], [])  # ...and that is fine here


if __name__ == "__main__":
    unittest.main()
