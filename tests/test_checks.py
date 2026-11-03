"""Each file, README, git and command check passes and fails on a tiny temp repo."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from helpers import AFTER, BEFORE, CHECK_TYPES, RepoCase, commit_all, git, no_network, register_check, write
from preflight import gitutil, net
from preflight.checks import Context, run_checks
from preflight.checks.base import glob_regex, path_matches
from preflight.checks.gitchecks import parse_github_remote, redact_url
from preflight.model import CheckSpec, Ruleset, skipped

TODO = "TO" + "DO"  # built in pieces so this file does not trip the no-todo-markers check


class GlobTests(unittest.TestCase):
    def test_star_stays_inside_a_segment_and_double_star_crosses(self) -> None:
        self.assertTrue(path_matches("templates/*.yaml", "templates/room.yaml"))
        self.assertFalse(path_matches("templates/*.yaml", "templates/sub/room.yaml"))
        self.assertTrue(path_matches("docs/**/*.md", "docs/a/b/c.md"))
        self.assertTrue(path_matches("docs/**/*.md", "docs/c.md"))

    def test_matching_is_case_insensitive_and_any_depth_is_opt_in(self) -> None:
        self.assertTrue(path_matches("license*", "LICENSE.md"))
        self.assertFalse(path_matches(".env", "config/.env"))
        self.assertTrue(path_matches(".env", "config/.env", any_depth=True))
        self.assertTrue(path_matches("*.pem", "keys/server.PEM", any_depth=True))
        self.assertIsNotNone(glob_regex("[ab]?.txt").match("a1.txt"))


class FileExistsTests(RepoCase):
    def test_pass_and_fail(self) -> None:
        repo = self.repo({"LICENSE": "MIT", "README.md": "# x\n"})
        self.assertStatus(self.check(repo, "file_exists", paths=["LICENSE", "LICENSE.md"]), "PASS", "LICENSE")
        self.assertStatus(self.check(repo, "file_exists", paths=["CHANGELOG.md"]), "FAIL", "CHANGELOG.md")

    def test_untracked_file_counts_but_ignored_file_does_not(self) -> None:
        repo = self.repo({".gitignore": "secret.txt\n", "a.txt": "a"})
        write(repo, "LICENSE", "MIT")  # untracked, not ignored
        write(repo, "secret.txt", "x")  # ignored
        self.assertStatus(self.check(repo, "file_exists", paths=["LICENSE"]), "PASS")
        self.assertStatus(self.check(repo, "file_exists", paths=["secret.txt"]), "FAIL")

    def test_glob_and_case(self) -> None:
        repo = self.repo({"templates/room.yaml": "a: 1\n", "License.md": "MIT"})
        self.assertStatus(self.check(repo, "file_exists", paths=["templates/*.yaml"]), "PASS")
        self.assertStatus(self.check(repo, "file_exists", paths=["LICENSE.md"]), "PASS")


class FileAbsentTests(RepoCase):
    def test_tracked_env_fails_and_names_the_file(self) -> None:
        repo = self.repo({".env": "A=1\n", "ok.txt": "x"})
        self.assertStatus(self.check(repo, "file_absent", paths=[".env", "*.pem"]), "FAIL", ".env")

    def test_untracked_or_ignored_env_passes(self) -> None:
        repo = self.repo({".gitignore": ".env\n", "ok.txt": "x"})
        write(repo, ".env", "A=1\n")
        self.assertStatus(self.check(repo, "file_absent", paths=[".env"]), "PASS")

    def test_matches_at_any_depth_and_spares_env_example(self) -> None:
        repo = self.repo({"deploy/server.pem": "x", ".env.example": "A=\n"})
        self.assertStatus(self.check(repo, "file_absent", paths=["*.pem"]), "FAIL", "deploy/server.pem")
        self.assertStatus(self.check(repo, "file_absent", paths=[".env"]), "PASS")


class ReadmeHeadingsTests(RepoCase):
    README = (
        "# Title\n\n## The problem\nTeams lose track of the room.\n\n## What it does\nIt prints names.\n\n"
        "## How the channel is set up\nCreate a channel.\n"
    )

    def test_pass_when_every_heading_is_present(self) -> None:
        repo = self.repo({"README.md": self.README})
        out = self.check(repo, "readme_headings", headings=["Problem", "What it does", "channel"])
        self.assertStatus(out, "PASS", "all 3 headings")

    def test_fail_names_the_missing_headings(self) -> None:
        repo = self.repo({"README.md": self.README})
        out = self.check(repo, "readme_headings", headings=["Problem", "Agents", "Reused"])
        self.assertStatus(out, "FAIL", "missing heading(s): Agents, Reused")

    def test_alternatives_accept_either_wording(self) -> None:
        repo = self.repo({"README.md": "## Our channel\nhello there friend\n"})
        out = self.check(repo, "readme_headings", headings=["How the channel is set up|channel"])
        self.assertStatus(out, "PASS")

    def test_a_hash_comment_in_a_code_block_is_not_a_heading(self) -> None:
        repo = self.repo({"README.md": "## Run it\n```sh\n# Agents\nmake\n```\n"})
        out = self.check(repo, "readme_headings", headings=["Agents"])
        self.assertStatus(out, "FAIL", "Agents")

    def test_min_chars_rejects_an_empty_section(self) -> None:
        repo = self.repo({"README.md": "## Problem\n_coming_\n\n## Run it\n" + "word " * 20})
        out = self.check(repo, "readme_headings", headings=["Problem", "Run it"], min_chars=40)
        self.assertStatus(out, "FAIL", "section too short: Problem")
        self.assertNotIn("Run it (", out.detail)

    def test_a_section_includes_its_subsections(self) -> None:
        repo = self.repo({"README.md": "## Agents\n### scout\n" + "reads rules and drafts checks. " * 3})
        self.assertStatus(self.check(repo, "readme_headings", headings=["Agents"], min_chars=40), "PASS")

    def test_lookup_is_case_insensitive_and_missing_readme_fails(self) -> None:
        repo = self.repo({"readme.md": "## Problem\nx\n"})
        self.assertStatus(self.check(repo, "readme_headings", headings=["Problem"]), "PASS")
        empty = self.repo({"a.txt": "x"}, name="empty")
        self.assertStatus(self.check(empty, "readme_headings", headings=["Problem"]), "FAIL", "README.md not found")


class FileContainsTests(RepoCase):
    def test_pass_fail_and_flags(self) -> None:
        repo = self.repo({"README.md": "Built with Switch\n"})
        self.assertStatus(self.check(repo, "file_contains", file="README.md", pattern="Switch"), "PASS")
        self.assertStatus(self.check(repo, "file_contains", file="README.md", pattern="switch"), "FAIL")
        self.assertStatus(self.check(repo, "file_contains", file="README.md", pattern="switch", flags="i"), "PASS")
        self.assertStatus(self.check(repo, "file_contains", file="NOPE.md", pattern="x"), "FAIL", "NOPE.md not found")


class TextAbsentTests(RepoCase):
    def test_finds_the_marker_with_file_and_line(self) -> None:
        repo = self.repo({"a.py": f"x = 1\n# {TODO}: later\n", "b.md": "fine\n"})
        out = self.check(repo, "text_absent", pattern=TODO)
        self.assertStatus(out, "FAIL", "a.py:2")

    def test_passes_when_clean_and_globs_limit_the_scan(self) -> None:
        repo = self.repo({"a.py": f"# {TODO}\n", "b.md": "fine\n"})
        self.assertStatus(self.check(repo, "text_absent", pattern=TODO, globs=["*.md"]), "PASS")
        self.assertStatus(self.check(repo, "text_absent", pattern=TODO, globs=["*.py"]), "FAIL")

    def test_skips_binary_and_big_files(self) -> None:
        big = ("x" * 1_100_000) + TODO
        repo = self.repo({"blob.bin": b"\0\0" + TODO.encode(), "big.txt": big, "ok.txt": "fine"})
        out = self.check(repo, "text_absent", pattern=TODO)
        self.assertStatus(out, "PASS", "1 tracked text file")

    def test_untracked_files_are_not_scanned(self) -> None:
        repo = self.repo({"ok.txt": "fine"})
        write(repo, "new.txt", TODO)
        self.assertStatus(self.check(repo, "text_absent", pattern=TODO), "PASS")


class MaxFileMbTests(RepoCase):
    def test_pass_and_fail(self) -> None:
        repo = self.repo({"small.txt": "x", "big.bin": b"\1" * 30_000})
        self.assertStatus(self.check(repo, "max_file_mb", limit_mb=1), "PASS", "no tracked file over 1 MB")
        out = self.check(repo, "max_file_mb", limit_mb=0.01)  # 10 485 bytes
        self.assertStatus(out, "FAIL", "big.bin")
        self.assertIn("1 file(s) over 0.01 MB", out.detail)


class GitCleanTests(RepoCase):
    def test_clean_modified_and_untracked(self) -> None:
        repo = self.repo({"a.txt": "a"})
        self.assertStatus(self.check(repo, "git_clean"), "PASS")
        write(repo, "a.txt", "changed")
        self.assertStatus(self.check(repo, "git_clean"), "FAIL", "1 uncommitted", "a.txt")
        git(repo, "checkout", "--", "a.txt")
        write(repo, "new.txt", "n")
        self.assertStatus(self.check(repo, "git_clean"), "FAIL", "?? new.txt")

    def test_the_preflight_folder_is_ignored_even_when_tracked(self) -> None:
        repo = self.repo({"a.txt": "a", ".preflight/verdict.json": "{}"})  # committed on purpose
        write(repo, ".preflight/verdict.json", '{"changed": true}')
        write(repo, ".preflight/other.json", "{}")
        self.assertStatus(self.check(repo, "git_clean"), "PASS")

    def test_not_a_repo_is_a_skip_with_a_reason(self) -> None:
        folder = self.tmp / "plain"
        folder.mkdir()
        out = self.check(folder, "git_clean")
        self.assertStatus(out, "SKIP", "git problem")


class GitFirstCommitAfterTests(RepoCase):
    START = "2026-11-03T18:31:00Z"

    def test_pass_when_every_commit_is_after_the_start(self) -> None:
        repo = self.repo({"a.txt": "a"}, when=AFTER)
        write(repo, "b.txt", "b")
        commit_all(repo, "second", "2026-11-05T10:00:00+05:30")
        self.assertStatus(self.check(repo, "git_first_commit_after", after=self.START), "PASS", "2026-11-04T04:30:00Z")

    def test_fail_names_the_earliest_commit_date_and_sha(self) -> None:
        repo = self.repo({"a.txt": "a"}, when=BEFORE)
        write(repo, "b.txt", "b")
        sha = commit_all(repo, "later", AFTER)
        first = git(repo, "rev-list", "--max-parents=0", "HEAD")
        out = self.check(repo, "git_first_commit_after", after=self.START)
        self.assertStatus(out, "FAIL", "2026-11-02T04:30:00Z", first[:7], "1 of 2")
        self.assertNotIn(sha[:7], out.detail)

    def test_an_early_commit_on_another_branch_still_counts(self) -> None:
        repo = self.repo({"a.txt": "a"}, when=AFTER)
        git(repo, "checkout", "-q", "-b", "old")
        write(repo, "old.txt", "o")
        commit_all(repo, "old work", BEFORE)
        git(repo, "checkout", "-q", "main")
        self.assertStatus(self.check(repo, "git_first_commit_after", after=self.START), "FAIL", "2026-11-02")

    def test_the_boundary_is_inclusive(self) -> None:
        repo = self.repo({"a.txt": "a"}, when="2026-11-03T18:31:00+00:00")
        self.assertStatus(self.check(repo, "git_first_commit_after", after=self.START), "PASS")

    def test_empty_repo_fails(self) -> None:
        repo = self.repo(None)
        self.assertStatus(self.check(repo, "git_first_commit_after", after=self.START), "FAIL", "no commits")


class GitRemotePublicTests(RepoCase):
    def repo_with_origin(self, url: str | None, name: str = "proj") -> Path:
        repo = self.repo({"a.txt": "a"}, name=name)
        if url:
            git(repo, "remote", "add", "origin", url)
        return repo

    @staticmethod
    def answer(status: int, body: object = None, calls: list | None = None):
        def fetch(url: str, **kwargs: object) -> net.Response:
            if calls is not None:
                calls.append((url, kwargs))
            return net.Response(status, url, {}, json.dumps(body).encode() if body is not None else b"")
        return fetch

    def test_public_private_and_missing(self) -> None:
        repo = self.repo_with_origin("https://github.com/Asmit-Rai/x.git")
        calls: list = []
        out = self.check(repo, "git_remote_public", fetch=self.answer(200, {"private": False}, calls))
        self.assertStatus(out, "PASS", "github.com/Asmit-Rai/x is public")
        self.assertEqual(calls[0][0], "https://api.github.com/repos/Asmit-Rai/x")
        self.assertStatus(self.check(repo, "git_remote_public", fetch=self.answer(200, {"private": True})), "FAIL", "is private")
        self.assertStatus(self.check(repo, "git_remote_public", fetch=self.answer(404)), "FAIL", "not found or private")

    def test_ssh_remote_form_is_understood(self) -> None:
        self.assertEqual(parse_github_remote("git@github.com:Asmit-Rai/x.git"), ("Asmit-Rai", "x"))
        self.assertEqual(parse_github_remote("ssh://git@github.com/o/n.git"), ("o", "n"))
        self.assertEqual(parse_github_remote("https://github.com/o/my.repo/"), ("o", "my.repo"))
        self.assertIsNone(parse_github_remote("https://gitlab.com/o/n.git"))

    def test_no_origin_and_non_github_origin_fail(self) -> None:
        self.assertStatus(self.check(self.repo_with_origin(None), "git_remote_public", fetch=no_network), "FAIL", "no 'origin'")
        repo = self.repo_with_origin("https://user:hunter2@gitlab.com/o/n.git", name="other")
        out = self.check(repo, "git_remote_public", fetch=no_network)
        self.assertStatus(out, "FAIL", "gitlab.com")
        self.assertNotIn("hunter2", out.detail)
        self.assertEqual(redact_url("https://u:p@github.com/o/n"), "https://github.com/o/n")

    def test_offline_rate_limit_and_network_errors_are_skips(self) -> None:
        repo = self.repo_with_origin("https://github.com/o/n.git")
        self.assertStatus(self.check(repo, "git_remote_public", offline=True, fetch=no_network), "SKIP", "offline")
        self.assertStatus(self.check(repo, "git_remote_public", fetch=self.answer(403)), "SKIP", "rate limit")
        self.assertStatus(self.check(repo, "git_remote_public", fetch=self.answer(429)), "SKIP", "rate limit")
        self.assertStatus(self.check(repo, "git_remote_public", fetch=self.answer(502)), "SKIP", "502")

        def down(url: str, **kwargs: object) -> net.Response:
            raise net.NetError("timed out")

        self.assertStatus(self.check(repo, "git_remote_public", fetch=down), "SKIP", "timed out")
        self.assertStatus(self.check(repo, "git_remote_public", fetch=self.answer(200)), "SKIP")  # no body to read

    def test_github_token_is_sent_but_never_printed(self) -> None:
        repo = self.repo_with_origin("https://github.com/o/n.git")
        calls: list = []
        out = self.check(repo, "git_remote_public", fetch=self.answer(200, {"private": False}, calls), env={"GITHUB_TOKEN": "tok-value-123"})
        self.assertStatus(out, "PASS")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer tok-value-123")
        self.assertNotIn("tok-value-123", out.detail)

    def test_a_rejected_token_falls_back_to_an_anonymous_request(self) -> None:
        repo = self.repo_with_origin("https://github.com/o/n.git")
        calls: list = []

        def fetch(url: str, **kwargs: dict) -> net.Response:
            calls.append(kwargs.get("headers", {}))
            if "Authorization" in kwargs.get("headers", {}):
                return net.Response(401, url)
            return net.Response(200, url, {}, b'{"private": false}')

        out = self.check(repo, "git_remote_public", fetch=fetch, env={"GITHUB_TOKEN": "stale"})
        self.assertStatus(out, "PASS")
        self.assertEqual(len(calls), 2)


class CommandTests(RepoCase):
    def test_pass_shows_exit_code_and_last_line(self) -> None:
        repo = self.repo({"a.txt": "a"})
        out = self.check(repo, "command", cmd="echo first; echo all good")
        self.assertStatus(out, "PASS", "exit 0", "all good")

    def test_fail_shows_the_exit_code(self) -> None:
        repo = self.repo({"a.txt": "a"})
        out = self.check(repo, "command", cmd="echo trouble >&2; exit 3")
        self.assertStatus(out, "FAIL", "exit 3", "trouble")

    def test_runs_in_the_project_folder(self) -> None:
        repo = self.repo({"marker.txt": "here"})
        self.assertStatus(self.check(repo, "command", cmd="test -f marker.txt"), "PASS")

    def test_timeout_kills_the_whole_process_group(self) -> None:
        repo = self.repo({"a.txt": "a"})
        out = self.check(repo, "command", cmd="sleep 30 & sleep 30", timeout=1)
        self.assertStatus(out, "FAIL", "timed out after 1 s")

    def test_secrets_in_command_output_are_masked(self) -> None:
        repo = self.repo({"a.txt": "a"})
        token = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        out = self.check(repo, "command", cmd=f"echo {token}")
        self.assertNotIn(token, out.detail)
        self.assertIn("ghp_****", out.detail)


class RunnerSafetyTests(RepoCase):
    def ruleset(self, *specs: CheckSpec) -> Ruleset:
        return Ruleset("t", 1, "", [], list(specs), "t.toml", "0" * 64)

    def test_a_crashing_check_becomes_a_skip_and_the_rest_still_run(self) -> None:
        def boom(ctx: Context, params: dict) -> None:
            raise RuntimeError("kaboom")

        repo = self.repo({"LICENSE": "MIT"})
        with mock.patch.dict(CHECK_TYPES):
            register_check("boom", boom)
            results = run_checks(
                self.ruleset(
                    CheckSpec("a", "A", "boom", True, "", {}),
                    CheckSpec("b", "B", "file_exists", True, "", {"paths": ["LICENSE"]}),
                ),
                Context(repo, fetch=no_network),
            )
        self.assertEqual([r.status for r in results], ["SKIP", "PASS"])
        self.assertIn("kaboom", results[0].detail)
        self.assertIn("cannot verify", results[0].detail)

    def test_a_failing_optional_check_is_a_warning_and_a_skipped_required_check_blocks(self) -> None:
        repo = self.repo({"LICENSE": "MIT"})
        git(repo, "remote", "add", "origin", "https://github.com/o/n.git")
        results = run_checks(
            self.ruleset(
                CheckSpec("opt", "Optional", "file_exists", False, "", {"paths": ["NOPE"]}),
                CheckSpec("net", "Network", "git_remote_public", True, "", {}),
            ),
            Context(repo, offline=True, fetch=no_network),
        )
        self.assertEqual([r.status for r in results], ["WARN", "SKIP"])

    def test_only_marks_the_other_checks_as_skipped(self) -> None:
        repo = self.repo({"LICENSE": "MIT"})
        rules = self.ruleset(
            CheckSpec("a", "A", "file_exists", True, "", {"paths": ["LICENSE"]}),
            CheckSpec("b", "B", "file_exists", True, "", {"paths": ["LICENSE"]}),
        )
        results = run_checks(rules, Context(repo), only={"a"})
        self.assertEqual([r.status for r in results], ["PASS", "SKIP"])
        self.assertIn("--only", results[1].detail)

    def test_unknown_type_is_a_skip_not_a_crash(self) -> None:
        repo = self.repo({"a.txt": "a"})
        out = self.check(repo, "nope")
        self.assertStatus(out, "SKIP", "unknown check type")

    def test_git_error_message_is_reported(self) -> None:
        with mock.patch.object(gitutil, "run", side_effect=gitutil.GitError("git is not installed")):
            out = self.check(self.tmp, "git_clean")
        self.assertStatus(out, "SKIP", "git is not installed")


if __name__ == "__main__":
    unittest.main()
