"""The `run` command: exit codes, formats, files it writes, and the real `python3 -m preflight` entry point."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from helpers import FIXTURES, RepoCase, git, read_verdict, run_cli, run_module, write
from preflight import cli
from preflight.checks import CHECK_TYPES
from preflight.verdict import check_gate

TINY = str(FIXTURES / "tiny.toml")
SCHEMA_TOP = ["schema", "ruleset", "project", "commit", "dirty", "created_at", "results", "summary", "verdict", "blocking"]


class RunExitCodeTests(RepoCase):
    def test_exit_0_only_when_every_required_check_passed(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", TINY)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().splitlines()[-1], "VERDICT: READY")

    def test_exit_1_when_a_required_check_failed(self) -> None:
        repo = self.repo({"other.txt": "x"})
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", TINY)
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines()[-1], "VERDICT: NOT READY (blocking: readme)")

    def test_exit_1_when_a_required_check_was_skipped(self) -> None:
        rules = self.tmp / "net.toml"
        rules.write_text(
            '[meta]\nname = "n"\nversion = 1\nsource = "https://e.org"\n'
            '[[check]]\nid = "repo-public"\ntitle = "t"\ntype = "git_remote_public"\nfix_hint = "f"\n',
            encoding="utf-8",
        )
        repo = self.repo({"README.md": "# x\n"})
        git(repo, "remote", "add", "origin", "https://github.com/o/n.git")
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", str(rules), "--offline")
        self.assertEqual(code, 1)
        self.assertIn("SKIP", out)
        self.assertIn("offline mode", out)
        self.assertEqual(out.strip().splitlines()[-1], "VERDICT: NOT READY (blocking: repo-public)")

    def test_exit_2_for_usage_and_config_errors(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        code, _, err = run_cli("run", "--project", str(self.tmp / "nowhere"), "--rules", TINY)
        self.assertEqual(code, 2)
        self.assertIn("project folder not found", err)

        bad = self.tmp / "bad.toml"
        bad.write_text('[meta]\nname = "x"\nversion = 1\n[[check]]\nid = "a"\ntitle = "A"\ntype = "bogus"\n', encoding="utf-8")
        code, _, err = run_cli("run", "--project", str(repo), "--rules", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("unknown check type 'bogus'", err)

        code, _, err = run_cli("run", "--project", str(repo), "--rules", str(self.tmp / "absent.toml"))
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

        code, _, err = run_cli("run", "--project", str(repo), "--rules", TINY, "--only", "readme,nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown check id(s): nope", err)

        self.assertEqual(run_cli("run", "--project", str(repo))[0], 2)  # --rules is required
        self.assertEqual(run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "xml")[0], 2)
        self.assertEqual(run_cli("frobnicate")[0], 2)
        self.assertEqual(run_cli()[0], 2)

    def test_the_real_entry_point_returns_the_same_codes(self) -> None:
        green = self.repo({"README.md": "# x\n"})
        red = self.repo({"other.txt": "x"})
        self.assertEqual(run_module("run", "--project", str(green), "--rules", TINY, "--format", "chat").returncode, 0)
        proc = run_module("run", "--project", str(red), "--rules", TINY, "--format", "chat")
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stdout.strip().endswith("VERDICT: NOT READY (blocking: readme)"))
        self.assertEqual(run_module("run", "--project", str(red)).returncode, 2)
        self.assertEqual(run_module().returncode, 2)
        self.assertEqual(run_module("lint", "--rules", TINY).returncode, 0)


class InternalErrorTests(RepoCase):
    def test_a_crash_exits_2_and_never_looks_like_a_verdict(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        with mock.patch.object(cli, "run_checks", side_effect=RuntimeError("boom")):
            code, out, err = run_cli("run", "--project", str(repo), "--rules", TINY)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("internal error: RuntimeError: boom", err)
        self.assertFalse((repo / ".preflight").exists())
        with mock.patch.object(cli, "check_gate", side_effect=RuntimeError("boom")):
            code, out, _ = run_cli("verdict", "--project", str(repo))
        self.assertEqual(code, 2)
        self.assertNotIn("GATE OK", out)


class RunOutputTests(RepoCase):
    def test_verdict_json_follows_the_schema(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "json")
        doc = read_verdict(repo)
        self.assertEqual(list(doc), SCHEMA_TOP)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["ruleset"], {"name": "Tiny fixture", "version": 3, "source": "https://example.org/tiny"})
        self.assertEqual(doc["project"], str(repo))
        self.assertRegex(doc["commit"], r"^[0-9a-f]{40}$")
        self.assertIs(doc["dirty"], False)
        self.assertRegex(doc["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(list(doc["results"][0]), ["id", "title", "type", "required", "status", "detail", "hint"])
        self.assertEqual(doc["results"][0]["hint"], "add a README")
        self.assertEqual(doc["summary"], {"pass": 2, "fail": 0, "warn": 0, "skip": 0})
        self.assertEqual((doc["verdict"], doc["blocking"]), ("READY", []))

    def test_json_format_prints_the_same_document_that_is_written(self) -> None:
        repo = self.repo({"other.txt": "x"})
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), read_verdict(repo))
        self.assertEqual(json.loads(out)["blocking"], ["readme"])

    def test_no_write_leaves_no_verdict_file(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        run_cli("run", "--project", str(repo), "--rules", TINY, "--no-write")
        self.assertFalse((repo / ".preflight").exists())

    def test_json_option_writes_a_copy_even_with_no_write(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        target = self.tmp / "out" / "report.json"
        code, _, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--no-write", "--json", str(target))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(target.read_text())["verdict"], "READY")
        self.assertFalse((repo / ".preflight").exists())

    def test_table_and_chat_formats(self) -> None:
        repo = self.repo({"other.txt": "x"})
        _, table, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "table")
        self.assertTrue(table.startswith("PREFLIGHT  Tiny fixture (ruleset v3)"))
        self.assertIn("SUMMARY: 1 pass, 1 fail, 0 warn, 0 skip", table)
        _, chat, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "chat")
        self.assertLessEqual(len(chat), 1800)
        self.assertIn("FAIL readme: no file matching README.md", chat)

    def test_only_runs_the_named_checks_and_the_rest_block_the_verdict(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--only", "readme")
        self.assertEqual(code, 1)  # `clean` was not run, so "ready" cannot be claimed
        self.assertIn("not run (excluded by --only)", out)
        self.assertEqual(out.strip().splitlines()[-1], "VERDICT: NOT READY (blocking: clean)")
        code, _, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--only", "readme,clean")
        self.assertEqual(code, 0)

    def test_dirty_is_recorded_after_the_checks_ran(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        rules = self.tmp / "dirtier.toml"
        rules.write_text(
            '[meta]\nname = "d"\nversion = 1\nsource = "https://e.org"\n'
            '[[check]]\nid = "mess"\ntitle = "t"\ntype = "command"\ncmd = "echo hi > generated.txt"\nfix_hint = "f"\n',
            encoding="utf-8",
        )
        code, out, err = run_cli("run", "--project", str(repo), "--rules", str(rules), "--format", "chat")
        self.assertEqual(code, 0)
        self.assertTrue(read_verdict(repo)["dirty"])
        self.assertIn("uncommitted changes", err)
        gate = check_gate(repo)
        self.assertFalse(gate.ok)
        self.assertIn("working tree with uncommitted changes", gate.message)

    def test_a_project_that_is_not_a_git_repo_still_runs_and_never_gets_ready(self) -> None:
        folder = self.tmp / "plain"
        write(folder, "README.md", "# x\n")
        code, out, _ = run_cli("run", "--project", str(folder), "--rules", TINY)
        self.assertEqual(code, 1)
        self.assertIn("git problem", out)
        self.assertIsNone(read_verdict(folder)["commit"])


class RegistryTests(unittest.TestCase):
    def test_there_are_fourteen_check_types_in_the_brief_order(self) -> None:
        self.assertEqual(
            list(CHECK_TYPES),
            ["file_exists", "file_absent", "readme_headings", "file_contains", "text_absent", "secret_scan",
             "git_first_commit_after", "git_clean", "git_remote_public", "links_ok", "video_link_present",
             "video_max_seconds", "max_file_mb", "command"],
        )
        for ctype in CHECK_TYPES.values():
            self.assertTrue(ctype.summary.strip(), ctype.name)

    def test_only_the_command_check_runs_code(self) -> None:
        self.assertEqual([n for n, t in CHECK_TYPES.items() if t.runs_code], ["command"])
        self.assertEqual({n for n, t in CHECK_TYPES.items() if t.network}, {"git_remote_public", "links_ok", "video_max_seconds"})


if __name__ == "__main__":
    unittest.main()
