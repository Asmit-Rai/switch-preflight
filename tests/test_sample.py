"""The sample project generator: deterministic, exactly 6 planted defects, and an all-green twin."""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
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
    """Answers like a healthy internet: the made-up repo is public, the video is 19 s long."""
    if url.startswith("https://api.github.com/repos/example-team/sample-submission"):
        return net.Response(200, url, {}, b'{"private": false}')
    if url.startswith("https://www.youtube.com/watch?v=jNQXAC9IVRw"):
        return net.Response(200, url, {}, b'<html>"lengthSeconds":"19"</html>')
    return net.Response(200, url)


class SampleCase(RepoCase):
    def sample(self, name: str, fixed: bool = False) -> Path:
        dest = self.tmp / name
        make_sample.create_sample(dest, fixed=fixed)
        return dest

    def results(self, project: Path, **ctx: Any) -> dict[str, Any]:
        rules = load_ruleset(CONTEST)
        return {r.id: r for r in run_checks(rules, Context(project, **ctx))}


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
        broken = self.sample("broken")
        dates = git(broken, "log", "--reverse", "--format=%aI|%cI|%an <%ae>").splitlines()
        self.assertEqual(dates[0], "2026-11-02T10:00:00+05:30|2026-11-02T10:00:00+05:30|Sample Team <sample@example.com>")
        self.assertEqual(len(dates), 3)
        fixed = self.sample("fixed", fixed=True)
        first = git(fixed, "log", "--reverse", "--format=%aI").splitlines()[0]
        self.assertEqual(first, "2026-11-04T10:00:00+05:30")
        self.assertEqual(git(fixed, "config", "--get", "remote.origin.url"), "https://github.com/example-team/sample-submission.git")

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

    def test_the_broken_sample_fails_exactly_the_checks_of_those_six_defects(self) -> None:
        project = self.sample("broken")
        results = self.results(project, offline=True, fetch=no_network)  # offline: network checks SKIP
        failing = {i for i, r in results.items() if r.status == "FAIL"}
        expected = {check for _, _, checks in make_sample.DEFECTS for check in checks}
        self.assertEqual(failing, expected)
        self.assertEqual(len(failing), 7)  # 6 defects; the committed .env trips two checks
        for number, text, checks in make_sample.DEFECTS:
            for check in checks:
                self.assertEqual(results[check].status, "FAIL", f"defect {number} ({text}) should fail {check}")
        quiet = {i for i, r in results.items() if r.status == "PASS"}
        self.assertEqual(quiet, {"git-clean", "template-present", "tests-pass", "max-file-25mb", "no-todo-markers"})
        self.assertEqual({i for i, r in results.items() if r.status == "SKIP"}, NETWORK_CHECKS)

    def test_defect_details(self) -> None:
        results = self.results(self.sample("broken"), offline=True, fetch=no_network)
        self.assertIn("missing heading(s): Agents, Reused", results["readme-headings"].detail)
        self.assertIn("no file matching LICENSE", results["license"].detail)
        self.assertIn(".env:2 GitHub token (ghp_****)", results["secret-scan"].detail)
        self.assertEqual(results["no-env-file"].detail, "tracked in git: .env")
        self.assertIn("2026-11-02T04:30:00Z", results["first-commit-after-start"].detail)
        self.assertIn("docs.example.invalid/setup", results["links-ok"].detail)
        self.assertIn("no video link", results["video-link-present"].detail)

    def test_the_planted_token_is_a_runtime_built_fake_that_the_scanner_masks(self) -> None:
        token = make_sample.fake_github_token()
        self.assertEqual(len(token), 40)
        self.assertEqual([h.pattern for h in find_secrets(f"GITHUB_TOKEN={token}")], ["GitHub token"])
        source = (EXAMPLES / "make_sample.py").read_text()
        self.assertNotIn(token, source)
        self.assertEqual([line for line in source.splitlines() if find_secrets(line)], [])
        env = (self.sample("broken") / ".env").read_text()
        self.assertIn(token, env)  # it is in the generated repo only

    def test_the_broken_sample_is_not_ready_through_the_cli(self) -> None:
        project = self.sample("broken")
        code, out, _ = run_cli("run", "--project", str(project), "--rules", OFFLINE, "--format", "chat")
        self.assertEqual(code, 1)
        last = out.strip().splitlines()[-1]
        for check in ("readme-headings", "license", "secret-scan", "no-env-file", "first-commit-after-start"):
            self.assertIn(check, last)
        self.assertEqual(run_cli("verdict", "--project", str(project))[0], 1)
        code, out, _ = run_cli("pack", "--project", str(project), "--out", str(self.tmp / "pack.md"))
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "pack.md").exists())


class FixedSampleTests(SampleCase):
    def test_the_fixed_sample_is_ready_under_the_real_contest_ruleset(self) -> None:
        project = self.sample("fixed", fixed=True)
        results = self.results(project, fetch=fake_internet)  # a healthy internet, no real network
        self.assertEqual({i: r.status for i, r in results.items() if r.status != "PASS"}, {})
        self.assertIn("video is 0:19", results["video-max-180s"].detail)
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
        self.assertIn("https://www.youtube.com/watch?v=jNQXAC9IVRw", text)
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
