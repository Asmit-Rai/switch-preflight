"""The gate (`verdict`) and the submission pack: closed on anything stale, dirty or edited."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helpers import AFTER, FIXTURES, RepoCase, commit_all, git, read_verdict, run_cli, write
from preflight.timeutil import now_iso
from preflight.verdict import check_gate, verdict_path

TINY = str(FIXTURES / "tiny.toml")


class GateCase(RepoCase):
    """Builds a project that is READY under tiny.toml, then lets each test break it."""

    def ready_project(self, extra: dict[str, str] | None = None) -> Path:
        repo = self.repo({"README.md": "# Demo\n\nWatch https://youtu.be/dQw4w9WgXcQ\n", **(extra or {})})
        git(repo, "remote", "add", "origin", "git@github.com:Asmit-Rai/demo.git")
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "chat")
        self.assertEqual(code, 0, out)
        return repo

    def edit_verdict(self, repo: Path, change: Any) -> None:
        path = verdict_path(repo)
        doc = json.loads(path.read_text())
        change(doc)
        path.write_text(json.dumps(doc, indent=2))

    def assertClosed(self, repo: Path, *words: str, **kwargs: Any) -> str:
        gate = check_gate(repo, **kwargs)
        self.assertFalse(gate.ok, gate.message)
        self.assertTrue(gate.message.startswith("GATE CLOSED: "), gate.message)
        for word in words:
            self.assertIn(word, gate.message)
        return gate.message


class GateOpenTests(GateCase):
    def test_open_when_fresh_clean_and_green(self) -> None:
        repo = self.ready_project()
        gate = check_gate(repo)
        self.assertTrue(gate.ok)
        self.assertEqual(gate.message, f"GATE OK: ready at {git(repo, 'rev-parse', '--short=7', 'HEAD')}")
        code, out, _ = run_cli("verdict", "--project", str(repo))
        self.assertEqual((code, out.strip()), (0, gate.message))

    def test_the_preflight_folder_never_makes_the_tree_dirty(self) -> None:
        repo = self.ready_project()
        write(repo, ".preflight/notes.txt", "scratch")
        self.assertTrue(check_gate(repo).ok)


class GateClosedTests(GateCase):
    def test_no_verdict_file(self) -> None:
        repo = self.repo({"README.md": "# x\n"})
        self.assertClosed(repo, "no verdict file")
        code, out, _ = run_cli("verdict", "--project", str(repo))
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("GATE CLOSED: "))

    def test_project_folder_missing(self) -> None:
        self.assertClosed(self.tmp / "nowhere", "project folder not found")

    def test_file_is_not_json_or_has_the_wrong_shape(self) -> None:
        repo = self.ready_project()
        verdict_path(repo).write_text("{ not json")
        self.assertClosed(repo, "not valid JSON")
        verdict_path(repo).write_text("[1, 2]")
        self.assertClosed(repo, "not a JSON object")
        for mutate, words in (
            (lambda d: d.update(schema=2), "schema 2"),
            (lambda d: d.pop("results"), "'results'"),
            (lambda d: d.update(commit=5), "'commit'"),
            (lambda d: d.update(dirty="no"), "'dirty'"),
            (lambda d: d.update(created_at="yesterday"), "'created_at'"),
            (lambda d: d["ruleset"].pop("name"), "'ruleset'"),
            (lambda d: d["results"][0].pop("detail"), "'results'"),
            (lambda d: d["results"][0].update(status="MAYBE"), "impossible status"),
            (lambda d: d["results"].append(dict(d["results"][0])), "twice"),
            (lambda d: d.update(results=[], blocking=[], summary={"pass": 0, "fail": 0, "warn": 0, "skip": 0}), "nothing was verified"),
        ):
            repo = self.ready_project()
            self.edit_verdict(repo, mutate)
            self.assertClosed(repo, words)

    def test_not_ready_verdict(self) -> None:
        repo = self.repo({"other.txt": "x"})
        code, _, _ = run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "json")
        self.assertEqual(code, 1)
        self.assertClosed(repo, "verdict is NOT READY", "blocking: readme")

    def test_stale_commit(self) -> None:
        repo = self.ready_project()
        old = git(repo, "rev-parse", "--short=7", "HEAD")
        write(repo, "more.txt", "more")
        commit_all(repo, "another change", AFTER)
        new = git(repo, "rev-parse", "--short=7", "HEAD")
        self.assertClosed(repo, f"commit {old} but HEAD is {new}")

    def test_dirty_tree_modified_and_untracked(self) -> None:
        repo = self.ready_project()
        write(repo, "README.md", "# changed\n")
        self.assertClosed(repo, "uncommitted", "README.md")
        git(repo, "checkout", "--", "README.md")
        self.assertTrue(check_gate(repo).ok)
        write(repo, "new.txt", "n")
        self.assertClosed(repo, "uncommitted", "?? new.txt")

    def test_a_verdict_produced_on_a_dirty_tree_is_refused_even_if_the_tree_is_clean_now(self) -> None:
        repo = self.ready_project()
        self.edit_verdict(repo, lambda d: d.update(dirty=True))
        self.assertClosed(repo, "uncommitted changes")

    def test_no_commit_in_the_verdict(self) -> None:
        repo = self.ready_project()
        self.edit_verdict(repo, lambda d: d.update(commit=None))
        self.assertClosed(repo, "no commit")

    def test_not_a_git_repository(self) -> None:
        repo = self.ready_project()
        plain = self.tmp / "plain"
        (plain / ".preflight").mkdir(parents=True)
        (plain / ".preflight" / "verdict.json").write_text(verdict_path(repo).read_text())
        self.assertClosed(plain, "not a git repository")


class GateTamperTests(GateCase):
    def failing_project(self) -> Path:
        repo = self.repo({"other.txt": "x"})
        run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "json")
        return repo

    def test_editing_the_verdict_to_ready_while_a_result_failed_is_refused(self) -> None:
        repo = self.failing_project()
        self.edit_verdict(repo, lambda d: d.update(verdict="READY", blocking=[]))
        message = self.assertClosed(repo, "does not match its own results")
        self.assertNotIn("GATE OK", message)

    def test_each_derived_field_is_recomputed(self) -> None:
        for name, mutate in (
            ("verdict", lambda d: d.update(verdict="READY")),
            ("blocking", lambda d: d.update(blocking=[])),
            ("summary", lambda d: d.update(summary={"pass": 2, "fail": 0, "warn": 0, "skip": 0})),
        ):
            with self.subTest(edited=name):
                repo = self.failing_project()
                self.edit_verdict(repo, mutate)
                self.assertClosed(repo, "does not match its own results")

    def test_marking_the_failed_check_as_optional_is_refused(self) -> None:
        repo = self.failing_project()
        self.edit_verdict(repo, lambda d: [r.update(required=False) for r in d["results"] if r["status"] == "FAIL"])
        self.assertClosed(repo, "does not match its own results")

    def test_a_required_check_cannot_be_a_warning(self) -> None:
        repo = self.failing_project()
        self.edit_verdict(repo, lambda d: [r.update(status="WARN") for r in d["results"] if r["status"] == "FAIL"])
        self.assertClosed(repo, "impossible status")

    def test_a_file_with_no_required_checks_is_refused(self) -> None:
        repo = self.ready_project()
        self.edit_verdict(repo, lambda d: [r.update(required=False) for r in d["results"]])
        self.assertClosed(repo, "no required checks")


class GateAgeTests(GateCase):
    def test_older_than_max_age(self) -> None:
        repo = self.ready_project()
        stale = (datetime.now(timezone.utc) - timedelta(minutes=181)).astimezone().replace(microsecond=0).isoformat()
        self.edit_verdict(repo, lambda d: d.update(created_at=stale))
        self.assertClosed(repo, "min old (max 120)")
        self.assertTrue(check_gate(repo, max_age_min=240).ok)
        code, out, _ = run_cli("verdict", "--project", str(repo), "--max-age-min", "240")
        self.assertEqual(code, 0, out)
        code, out, _ = run_cli("verdict", "--project", str(repo), "--max-age-min", "60")
        self.assertEqual(code, 1)
        self.assertIn("min old (max 60)", out)

    def test_now_can_be_injected(self) -> None:
        repo = self.ready_project()
        later = datetime.now(timezone.utc) + timedelta(hours=3)
        self.assertClosed(repo, "min old", now=later)
        self.assertTrue(check_gate(repo, now=datetime.now(timezone.utc) + timedelta(minutes=10)).ok)

    def test_a_verdict_dated_in_the_future_is_refused(self) -> None:
        repo = self.ready_project()
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).astimezone().replace(microsecond=0).isoformat()
        self.edit_verdict(repo, lambda d: d.update(created_at=future))
        self.assertClosed(repo, "dated in the future")

    def test_timestamp_has_an_offset(self) -> None:
        self.assertRegex(now_iso(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")


class PackTests(GateCase):
    def pack(self, repo: Path, out: Path, *extra: str) -> tuple[int, str, str]:
        return run_cli("pack", "--project", str(repo), "--out", str(out), *extra)

    def test_pack_refuses_when_the_gate_is_closed_and_writes_nothing(self) -> None:
        repo = self.ready_project()
        write(repo, "dirty.txt", "x")
        out_file = self.tmp / "pack" / "SUBMISSION.md"
        code, out, _ = self.pack(repo, out_file)
        self.assertEqual(code, 1)
        self.assertIn("GATE CLOSED: working tree has 1 uncommitted change(s)", out)
        self.assertIn("PACK REFUSED", out)
        self.assertFalse(out_file.exists())
        self.assertFalse(out_file.parent.exists())

    def test_pack_refuses_without_any_verdict_and_for_a_not_ready_verdict(self) -> None:
        repo = self.repo({"other.txt": "x"})
        out_file = self.tmp / "p.md"
        self.assertEqual(self.pack(repo, out_file)[0], 1)
        run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "json")
        code, out, _ = self.pack(repo, out_file)
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", out)
        self.assertFalse(out_file.exists())

    def test_pack_refuses_after_a_new_commit(self) -> None:
        repo = self.ready_project()
        write(repo, "x.txt", "x")
        commit_all(repo, "later", AFTER)
        out_file = self.tmp / "p.md"
        code, out, _ = self.pack(repo, out_file)
        self.assertEqual(code, 1)
        self.assertIn("HEAD is", out)
        self.assertFalse(out_file.exists())

    def test_pack_is_written_when_the_gate_is_open(self) -> None:
        repo = self.ready_project()
        doc = read_verdict(repo)
        out_file = self.tmp / "out" / "SUBMISSION.md"
        code, out, _ = self.pack(repo, out_file)
        self.assertEqual(code, 0, out)
        self.assertIn("GATE OK: ready at", out)
        text = out_file.read_text()
        self.assertIn("# Submission pack: Demo", text)  # the README's H1
        self.assertIn("| Repository | https://github.com/Asmit-Rai/demo |", text)  # ssh origin, normalised
        self.assertIn("| Demo video | https://youtu.be/dQw4w9WgXcQ |", text)
        self.assertIn(f"`{doc['commit']}`", text)
        self.assertIn(f"| Verified at | {doc['created_at']} |", text)
        self.assertIn("| Verdict | **READY** |", text)
        self.assertIn("| 1 | `readme` | yes | PASS | found README.md |", text)
        self.assertIn("Summary: 2 pass, 0 fail, 0 warn, 0 skip.", text)
        self.assertIn("Tiny fixture v3", text)

    def test_meta_file_supplies_project_facts(self) -> None:
        repo = self.ready_project()
        meta = self.tmp / "preflight.toml"
        meta.write_text('[project]\nname = "Roll Call"\nteam = "Team AXA"\nvideo_url = "https://vimeo.com/1"\n', encoding="utf-8")
        out_file = self.tmp / "p.md"
        self.assertEqual(self.pack(repo, out_file, "--meta", str(meta))[0], 0)
        text = out_file.read_text()
        self.assertIn("# Submission pack: Roll Call", text)
        self.assertIn("| Team | Team AXA |", text)
        self.assertIn("| Demo video | https://vimeo.com/1 |", text)

    def test_meta_problems_are_usage_errors(self) -> None:
        repo = self.ready_project()
        code, _, err = self.pack(repo, self.tmp / "p.md", "--meta", str(self.tmp / "absent.toml"))
        self.assertEqual(code, 2)
        self.assertIn("--meta file not found", err)
        bad = self.tmp / "bad.toml"
        bad.write_text("[project\n", encoding="utf-8")
        self.assertEqual(self.pack(repo, self.tmp / "p.md", "--meta", str(bad))[0], 2)
        self.assertFalse((self.tmp / "p.md").exists())

    def test_pipes_in_details_do_not_break_the_table(self) -> None:
        repo = self.ready_project()
        self.edit_verdict(repo, lambda d: d["results"][0].update(detail="a | b"))
        out_file = self.tmp / "p.md"
        self.assertEqual(self.pack(repo, out_file)[0], 0)
        self.assertIn("a \\| b", out_file.read_text())

    def test_pack_without_origin_or_video_says_so(self) -> None:
        repo = self.repo({"README.md": "# Plain\n"})
        run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "json")
        out_file = self.tmp / "p.md"
        self.assertEqual(self.pack(repo, out_file)[0], 0)
        text = out_file.read_text()
        self.assertIn("(no origin remote)", text)
        self.assertIn("(no video link found in README)", text)
