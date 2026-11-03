"""Ruleset fingerprint: computed by the loader, shown in reports, and enforced by `--approved-sha`.

The Captain approves a check list in chat; the gate must be able to prove the verdict came from THAT
ruleset, not from a trivial one an agent ran instead.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helpers import AFTER, FIXTURES, RULES, RepoCase, commit_all, read_verdict, run_cli, run_module, write
from preflight.model import Result
from preflight.report import CHAT_LIMIT, format_chat, format_json, format_table
from preflight.ruleset import fingerprint, load_ruleset, parse_ruleset
from preflight.verdict import build_document, check_gate, validate_document, verdict_path

TINY = FIXTURES / "tiny.toml"
TINY_BYTES = TINY.read_bytes()
TINY_SHA = hashlib.sha256(TINY_BYTES).hexdigest()  # tiny.toml has no `extends`: the plain file hash

PARENT = """[meta]
name = "Parent"
version = 1
source = "https://example.org/parent"

[[check]]
id = "readme"
title = "README exists"
type = "file_exists"
paths = ["README.md"]
fix_hint = "add a README"
"""
CHILD = """[meta]
name = "Child"
version = 2
source = "https://example.org/child"
extends = "parent.toml"

[[check]]
id = "clean"
title = "Clean tree"
type = "git_clean"
fix_hint = "commit"
"""
TRIVIAL = """[meta]
name = "Trivial"
version = 1
source = "https://example.org/trivial"

[[check]]
id = "readme"
title = "README exists"
type = "file_exists"
paths = ["README.md"]
fix_hint = "add one"
"""
WRONG = "deadbeef"  # not a prefix of any real fingerprint used here


def prefixed(*blobs: bytes) -> str:
    """The documented fingerprint of an `extends` chain, written out independently of the code."""
    digest = hashlib.sha256()
    for blob in blobs:
        digest.update(b"%08d\n" % len(blob) + blob)
    return digest.hexdigest()


class FingerprintTests(RepoCase):
    def rules(self, name: str, text: str | bytes) -> Path:
        write(self.tmp, f"rules/{name}", text)
        return self.tmp / "rules" / name

    def test_a_plain_ruleset_is_the_sha256_of_its_file_bytes(self) -> None:
        loaded = load_ruleset(TINY)
        self.assertEqual(loaded.sha256, TINY_SHA)
        self.assertRegex(loaded.sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(load_ruleset(TINY).sha256, loaded.sha256)  # stable
        self.assertEqual(fingerprint([b"abc"]), hashlib.sha256(b"abc").hexdigest())
        copy = self.rules("renamed-copy.toml", TINY_BYTES)  # the path is not part of the fingerprint
        self.assertEqual(load_ruleset(copy).sha256, TINY_SHA)

    def test_changing_one_byte_changes_it(self) -> None:
        self.assertIn(b"Tiny fixture", TINY_BYTES)
        for label, edited in (
            ("a letter", TINY_BYTES.replace(b"Tiny fixture", b"Tiny fixturf")),
            ("a comment", TINY_BYTES + b"# one more line\n"),
            ("a trailing space", TINY_BYTES.replace(b'version = 3', b'version = 3 ')),
        ):
            path = self.rules("edited.toml", edited)
            self.assertNotEqual(load_ruleset(path).sha256, TINY_SHA, label)
            self.assertEqual(load_ruleset(path).sha256, hashlib.sha256(edited).hexdigest(), label)

    def test_extends_hashes_both_files_parent_first_with_length_prefixes(self) -> None:
        self.rules("parent.toml", PARENT)
        child = self.rules("child.toml", CHILD)
        expected = prefixed(PARENT.encode(), CHILD.encode())
        self.assertEqual(load_ruleset(child).sha256, expected)
        self.assertNotEqual(expected, hashlib.sha256(CHILD.encode()).hexdigest())  # not just the child
        self.assertNotEqual(expected, prefixed(CHILD.encode(), PARENT.encode()))  # order matters
        self.assertTrue(prefixed(b"ab", b"c") != prefixed(b"a", b"bc"))  # bytes cannot slide between files

    def test_changing_the_parent_or_the_child_changes_the_fingerprint(self) -> None:
        parent = self.rules("parent.toml", PARENT)
        child = self.rules("child.toml", CHILD)
        before = load_ruleset(child).sha256
        parent.write_text(PARENT.replace("README exists", "README exist5"))
        after_parent = load_ruleset(child).sha256
        self.assertNotEqual(after_parent, before)
        child.write_text(CHILD.replace("Clean tree", "Clean tre3"))
        self.assertNotIn(load_ruleset(child).sha256, (before, after_parent))
        parent.write_text(PARENT)
        child.write_text(CHILD)
        self.assertEqual(load_ruleset(child).sha256, before)  # restoring the bytes restores the fingerprint

    def test_a_chain_of_three_is_hashed_base_first(self) -> None:
        grand = PARENT.replace('name = "Parent"', 'name = "Grand"')
        middle = CHILD.replace('name = "Child"', 'name = "Middle"').replace('id = "clean"', 'id = "clean-tree"').replace(
            'extends = "parent.toml"', 'extends = "grand.toml"'
        )
        top = CHILD.replace('id = "clean"', 'id = "clean-top"').replace('extends = "parent.toml"', 'extends = "middle.toml"')
        self.rules("grand.toml", grand)
        self.rules("middle.toml", middle)
        path = self.rules("top.toml", top)
        self.assertEqual(load_ruleset(path).sha256, prefixed(grand.encode(), middle.encode(), top.encode()))

    def test_the_bundled_rulesets_have_a_fingerprint(self) -> None:
        for path in sorted(RULES.glob("*.toml")):
            self.assertRegex(load_ruleset(path).sha256, r"^[0-9a-f]{64}$", path.name)
        flip = RULES / "flip-the-switch.toml"
        if "extends" not in flip.read_text(encoding="utf-8"):
            self.assertEqual(load_ruleset(flip).sha256, hashlib.sha256(flip.read_bytes()).hexdigest())

    def test_a_ruleset_that_cannot_be_loaded_has_no_fingerprint(self) -> None:
        loaded, problems = parse_ruleset(self.tmp / "missing.toml")
        self.assertIsNone(loaded)
        self.assertTrue(problems)


class ShaCommandTests(RepoCase):
    def test_prints_exactly_twelve_hex_characters_and_a_newline(self) -> None:
        code, out, err = run_cli("sha", "--rules", str(TINY))
        self.assertEqual((code, out, err), (0, TINY_SHA[:12] + "\n", ""))

    def test_the_real_entry_point_prints_nothing_else(self) -> None:
        flip = RULES / "flip-the-switch.toml"
        proc = run_module("sha", "--rules", str(flip))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"^[0-9a-f]{12}\n$")
        self.assertEqual(proc.stdout, load_ruleset(flip).sha256[:12] + "\n")
        self.assertEqual(proc.stderr, "")

    def test_a_file_that_cannot_be_read_is_exit_2_with_empty_stdout(self) -> None:
        code, out, err = run_cli("sha", "--rules", str(self.tmp / "nowhere.toml"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cannot read", err)
        self.assertIn("is not a valid ruleset", err)

    def test_an_invalid_ruleset_is_exit_2_with_empty_stdout(self) -> None:
        for name, text in (
            ("not-toml.toml", "this is [not toml"),
            ("bad-type.toml", TRIVIAL.replace("file_exists", "no_such_type")),
            ("no-checks.toml", '[meta]\nname = "x"\nversion = 1\n'),
        ):
            write(self.tmp, name, text)
            code, out, err = run_cli("sha", "--rules", str(self.tmp / name))
            self.assertEqual((code, out), (2, ""), name)
            self.assertIn("is not a valid ruleset", err, name)

    def test_the_rules_option_is_required(self) -> None:
        code, out, _ = run_cli("sha")
        self.assertEqual((code, out), (2, ""))

    def test_it_agrees_with_the_header_and_the_verdict_file(self) -> None:
        repo = self.repo({"README.md": "# Demo\n"})
        _, sha12, _ = run_cli("sha", "--rules", str(TINY))
        _, chat, _ = run_cli("run", "--project", str(repo), "--rules", str(TINY), "--format", "chat")
        self.assertEqual(chat.splitlines()[0].split("sha256:")[1].split()[0], sha12.strip())
        self.assertEqual(read_verdict(repo)["ruleset"]["sha256"][:12], sha12.strip())


class ReportHeaderTests(RepoCase):
    COMMIT = "1a21df49955b2b19a0614032a4f73e8964395074"

    def doc(self, results: list[Result] | None = None, **changes: Any) -> dict[str, Any]:
        ruleset = dataclasses.replace(load_ruleset(TINY), **changes)
        results = results or [Result("a", "t", "git_clean", True, "PASS", "ok", "")]
        return build_document(ruleset, Path("/p"), self.COMMIT, False, results, "2026-11-04T09:12:00+05:30")

    def test_the_table_header_shows_name_version_and_fingerprint(self) -> None:
        lines = format_table(self.doc()).splitlines()
        self.assertEqual(lines[0], f"PREFLIGHT  ruleset: Tiny fixture v3 sha256:{TINY_SHA[:12]}")

    def test_the_chat_header_shows_name_version_and_fingerprint(self) -> None:
        lines = format_chat(self.doc()).splitlines()
        self.assertEqual(lines[0], f"PREFLIGHT ruleset: Tiny fixture v3 sha256:{TINY_SHA[:12]} @ 1a21df4")

    def test_the_chat_message_still_fits_the_budget_with_the_longer_header(self) -> None:
        results = [Result(f"check-number-{n}", "t", "git_clean", True, "FAIL", "long detail " * 30, "") for n in range(40)]
        text = format_chat(self.doc(results))
        self.assertLessEqual(len(text), CHAT_LIMIT)
        self.assertIn(f"sha256:{TINY_SHA[:12]}", text.splitlines()[0])
        self.assertTrue(text.splitlines()[-1].startswith("VERDICT: NOT READY"))

    def test_a_very_long_name_is_clipped_but_never_the_fingerprint(self) -> None:
        doc = self.doc(name="N" * 200)
        table_head = format_table(doc).splitlines()[0]
        chat_head = format_chat(doc).splitlines()[0]
        self.assertLessEqual(len(table_head), 100)
        self.assertLessEqual(len(chat_head), 100)
        for head in (table_head, chat_head):
            self.assertIn(f" v3 sha256:{TINY_SHA[:12]}", head)
            self.assertIn("...", head)
        self.assertTrue(chat_head.endswith(" @ 1a21df4"))

    def test_the_json_carries_the_full_hash(self) -> None:
        shown = json.loads(format_json(self.doc()))
        self.assertEqual(shown["ruleset"]["sha256"], TINY_SHA)
        self.assertEqual(list(shown["ruleset"]), ["name", "version", "source", "sha256"])

    def test_a_real_run_prints_the_header_in_table_and_chat(self) -> None:
        repo = self.repo({"README.md": "# Demo\n"})
        for fmt, prefix in (("table", "PREFLIGHT  "), ("chat", "PREFLIGHT ")):
            _, out, _ = run_cli("run", "--project", str(repo), "--rules", str(TINY), "--format", fmt)
            self.assertTrue(out.startswith(f"{prefix}ruleset: Tiny fixture v3 sha256:{TINY_SHA[:12]}"), out[:120])


class ApprovedShaCase(RepoCase):
    """A project that is READY under tiny.toml; each test then changes the ruleset, the file or the repo."""

    def ready_project(self, rules: Path = TINY) -> Path:
        repo = self.repo({"README.md": "# Demo\n"})
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", str(rules), "--format", "chat")
        self.assertEqual(code, 0, out)
        return repo

    def edit_verdict(self, repo: Path, change: Any) -> None:
        path = verdict_path(repo)
        doc = json.loads(path.read_text())
        change(doc)
        path.write_text(json.dumps(doc, indent=2))

    def closed_message(self, sha12: str, approved: str = WRONG) -> str:
        return (
            f"GATE CLOSED: ruleset was not the approved one (approved sha256:{approved}, verdict used sha256:{sha12})"
        )


class ApprovedShaGateTests(ApprovedShaCase):
    def test_without_an_approved_sha_the_gate_behaves_as_before(self) -> None:
        repo = self.ready_project()
        self.assertTrue(check_gate(repo).ok)
        code, out, _ = run_cli("verdict", "--project", str(repo))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("GATE OK: ready at "))

    def test_open_when_the_approved_prefix_matches(self) -> None:
        repo = self.ready_project()
        for approved in (TINY_SHA[:8], TINY_SHA[:12], TINY_SHA[:40], TINY_SHA, TINY_SHA[:12].upper(), TINY_SHA.upper()):
            gate = check_gate(repo, approved_sha=approved)
            self.assertTrue(gate.ok, f"{approved}: {gate.message}")
            code, out, _ = run_cli("verdict", "--project", str(repo), "--approved-sha", approved)
            self.assertEqual((code, out.strip()), (0, gate.message), approved)

    def test_closed_when_the_approved_sha_does_not_match(self) -> None:
        repo = self.ready_project()
        flipped = TINY_SHA[:-1] + ("0" if TINY_SHA[-1] != "0" else "1")  # right for 63 characters, wrong for the last
        for approved in (WRONG, TINY_SHA[:7] + ("0" if TINY_SHA[7] != "0" else "1"), flipped):
            gate = check_gate(repo, approved_sha=approved)
            self.assertFalse(gate.ok)
            self.assertEqual(gate.message, self.closed_message(TINY_SHA[:12], approved))
            code, out, _ = run_cli("verdict", "--project", str(repo), "--approved-sha", approved)
            self.assertEqual((code, out), (1, gate.message + "\n"))

    def test_the_message_shows_the_approved_value_as_given(self) -> None:
        repo = self.ready_project()
        self.assertEqual(check_gate(repo, approved_sha="DEADBEEF").message, self.closed_message(TINY_SHA[:12], "DEADBEEF"))

    def test_a_tampered_fingerprint_in_verdict_json_closes_the_gate(self) -> None:
        repo = self.ready_project()
        self.edit_verdict(repo, lambda d: d["ruleset"].update(sha256="0" * 64))
        self.assertEqual(check_gate(repo, approved_sha=TINY_SHA[:12]).message, self.closed_message("0" * 12, TINY_SHA[:12]))
        self.edit_verdict(repo, lambda d: d["ruleset"].update(sha256=TINY_SHA[:-1] + ("0" if TINY_SHA[-1] != "0" else "1")))
        self.assertFalse(check_gate(repo, approved_sha=TINY_SHA).ok)  # the full 64 characters are compared

    def test_a_verdict_without_a_valid_fingerprint_is_malformed(self) -> None:
        repo = self.ready_project()
        pristine = verdict_path(repo).read_text()
        for label, mutate in (
            ("missing", lambda d: d["ruleset"].pop("sha256")),
            ("63 characters", lambda d: d["ruleset"].update(sha256=TINY_SHA[:63])),
            ("65 characters", lambda d: d["ruleset"].update(sha256=TINY_SHA + "0")),
            ("not hex", lambda d: d["ruleset"].update(sha256="g" * 64)),
            ("trailing newline", lambda d: d["ruleset"].update(sha256=TINY_SHA[:63] + "\n")),
            ("a number", lambda d: d["ruleset"].update(sha256=12345678)),
            ("null", lambda d: d["ruleset"].update(sha256=None)),
        ):
            verdict_path(repo).write_text(pristine)
            self.edit_verdict(repo, mutate)
            for approved in (None, TINY_SHA[:12]):  # the schema check comes first, approved or not
                gate = check_gate(repo, approved_sha=approved)
                self.assertFalse(gate.ok, label)
                self.assertEqual(gate.message, "GATE CLOSED: verdict.json has a malformed 'ruleset' field", label)
        verdict_path(repo).write_text(pristine)
        self.assertTrue(check_gate(repo, approved_sha=TINY_SHA[:12]).ok)

    def test_validate_document_requires_a_64_hex_fingerprint(self) -> None:
        repo = self.ready_project()
        doc = read_verdict(repo)
        self.assertIsNone(validate_document(doc))
        doc["ruleset"]["sha256"] = TINY_SHA.upper()  # case does not matter, length and alphabet do
        self.assertIsNone(validate_document(doc))
        for bad in (None, "", "abc", TINY_SHA[:-1], "z" * 64, 7):
            doc["ruleset"]["sha256"] = bad
            self.assertEqual(validate_document(doc), "verdict.json has a malformed 'ruleset' field", repr(bad))
        del doc["ruleset"]["sha256"]
        self.assertEqual(validate_document(doc), "verdict.json has a malformed 'ruleset' field")

    def test_the_approved_sha_is_checked_before_the_commit_and_age_checks(self) -> None:
        repo = self.ready_project()
        wrong = self.closed_message(TINY_SHA[:12])
        # an old verdict
        later = datetime.now(timezone.utc) + timedelta(hours=5)
        self.assertEqual(check_gate(repo, now=later, approved_sha=WRONG).message, wrong)
        self.assertIn("min old", check_gate(repo, now=later, approved_sha=TINY_SHA[:12]).message)
        # a dirty tree
        write(repo, "new.txt", "n")
        self.assertEqual(check_gate(repo, approved_sha=WRONG).message, wrong)
        self.assertIn("uncommitted", check_gate(repo, approved_sha=TINY_SHA[:12]).message)
        # a new commit since the run
        commit_all(repo, "another change", AFTER)
        self.assertEqual(check_gate(repo, approved_sha=WRONG).message, wrong)
        self.assertIn("but HEAD is", check_gate(repo, approved_sha=TINY_SHA[:12]).message)

    def test_an_invalid_value_given_to_the_function_never_approves_everything(self) -> None:
        repo = self.ready_project()
        for bad in ("", "abc", "abcdef0", "a" * 65, "xyz12345", "sha256:" + TINY_SHA[:12], TINY_SHA[:12] + "\n"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                check_gate(repo, approved_sha=bad)


class ApprovedShaCliTests(ApprovedShaCase):
    BAD = ("", "abc", "abcdef0", "a" * 65, "xyz12345", "sha256:abcdef012345", " abcdef012345", "abcdef012345\n", "abcdef01 ")

    def test_an_invalid_hex_is_a_usage_error_for_verdict_and_pack(self) -> None:
        repo = self.ready_project()
        out_file = self.tmp / "pack.md"
        for bad in self.BAD:
            for argv in (("verdict", "--project", str(repo)), ("pack", "--project", str(repo), "--out", str(out_file))):
                code, out, err = run_cli(*argv, "--approved-sha", bad)
                self.assertEqual(code, 2, f"{argv[0]} {bad!r}")
                self.assertEqual(out, "", f"{argv[0]} {bad!r}")
                self.assertIn("--approved-sha", err)
                self.assertIn("8 to 64 hexadecimal characters", err)
        self.assertFalse(out_file.exists())

    def test_pack_refuses_with_the_wrong_approved_sha_and_writes_with_the_right_one(self) -> None:
        repo = self.ready_project()
        out_file = self.tmp / "out" / "SUBMISSION.md"
        code, out, _ = run_cli("pack", "--project", str(repo), "--out", str(out_file), "--approved-sha", WRONG)
        self.assertEqual(code, 1)
        self.assertIn(self.closed_message(TINY_SHA[:12]), out)
        self.assertIn("PACK REFUSED", out)
        self.assertFalse(out_file.exists())
        code, out, _ = run_cli("pack", "--project", str(repo), "--out", str(out_file), "--approved-sha", TINY_SHA[:8])
        self.assertEqual(code, 0, out)
        self.assertIn("GATE OK", out)
        self.assertIn("PACK WRITTEN", out)
        self.assertTrue(out_file.is_file())

    def test_pack_refuses_a_tampered_fingerprint(self) -> None:
        repo = self.ready_project()
        self.edit_verdict(repo, lambda d: d["ruleset"].update(sha256="f" * 64))
        out_file = self.tmp / "tampered.md"
        code, out, _ = run_cli("pack", "--project", str(repo), "--out", str(out_file), "--approved-sha", TINY_SHA[:12])
        self.assertEqual(code, 1)
        self.assertIn("ruleset was not the approved one", out)
        self.assertFalse(out_file.exists())

    def test_the_real_entry_point_enforces_it(self) -> None:
        repo = self.ready_project()
        good = run_module("verdict", "--project", str(repo), "--approved-sha", TINY_SHA[:12])
        self.assertEqual((good.returncode, good.stdout.startswith("GATE OK: ready at ")), (0, True), good.stderr)
        bad = run_module("verdict", "--project", str(repo), "--approved-sha", WRONG)
        self.assertEqual((bad.returncode, bad.stdout), (1, self.closed_message(TINY_SHA[:12]) + "\n"))
        junk = run_module("verdict", "--project", str(repo), "--approved-sha", "nothex")
        self.assertEqual((junk.returncode, junk.stdout), (2, ""))


class SwappedRulesetTests(ApprovedShaCase):
    """The reason for all this: a verdict from a different ruleset must not pass for the approved one."""

    def test_a_trivial_ruleset_run_by_an_agent_does_not_open_the_approved_gate(self) -> None:
        trivial = self.tmp / "trivial.toml"
        trivial.write_text(TRIVIAL)
        repo = self.ready_project(trivial)  # READY, but under the wrong checks
        self.assertTrue(check_gate(repo).ok)  # without an approved sha nothing tells the two apart
        _, approved, _ = run_cli("sha", "--rules", str(TINY))
        self.assertEqual(
            check_gate(repo, approved_sha=approved.strip()).message,
            self.closed_message(hashlib.sha256(TRIVIAL.encode()).hexdigest()[:12], approved.strip()),
        )

    def test_editing_a_parent_ruleset_after_approval_closes_the_gate(self) -> None:
        parent = self.tmp / "parent.toml"
        child = self.tmp / "child.toml"
        parent.write_text(PARENT)
        child.write_text(CHILD)
        _, approved, _ = run_cli("sha", "--rules", str(child))
        repo = self.ready_project(child)
        self.assertTrue(check_gate(repo, approved_sha=approved.strip()).ok)
        parent.write_text(PARENT.replace("add a README", "please add a README"))  # a different ruleset, same checks
        code, out, _ = run_cli("run", "--project", str(repo), "--rules", str(child), "--format", "chat")
        self.assertEqual(code, 0, out)  # the changed ruleset is still READY here ...
        gate = check_gate(repo, approved_sha=approved.strip())
        self.assertFalse(gate.ok)  # ... but it is not the one the Captain approved
        self.assertIn("ruleset was not the approved one", gate.message)
        parent.write_text(PARENT)
        run_cli("run", "--project", str(repo), "--rules", str(child), "--format", "chat")
        self.assertTrue(check_gate(repo, approved_sha=approved.strip()).ok)


if __name__ == "__main__":
    unittest.main()
