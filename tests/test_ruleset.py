"""Ruleset loading and `lint`: it must catch bad rulesets and warn about code-running checks."""

from __future__ import annotations

import unittest
from pathlib import Path

from helpers import EXAMPLES, FIXTURES, RULES, RepoCase, run_cli
from preflight.checks import CHECK_TYPES
from preflight.ruleset import RulesetError, load_ruleset, parse_ruleset

META = '[meta]\nname = "T"\nversion = 1\nsource = "https://example.org/r"\n'


def check(body: str) -> str:
    return META + "\n[[check]]\nfix_hint = \"fix\"\n" + body


class LintTests(RepoCase):
    def lint(self, text: str) -> list[str]:
        path = self.tmp / "rules.toml"
        path.write_text(text, encoding="utf-8")
        _, problems = parse_ruleset(path)
        return [str(p) for p in problems]

    def errors(self, text: str) -> list[str]:
        return [p for p in self.lint(text) if p.startswith("ERROR")]

    def assertError(self, text: str, *words: str) -> None:
        errors = self.errors(text)
        self.assertTrue(errors, "expected an ERROR but lint found none")
        joined = "\n".join(errors)
        for word in words:
            self.assertIn(word, joined)

    def test_a_minimal_good_ruleset_has_no_problems(self) -> None:
        self.assertEqual(self.lint(check('id = "readme"\ntitle = "README"\ntype = "file_exists"\npaths = ["README.md"]\n')), [])

    def test_unknown_check_type(self) -> None:
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "bogus"\n'), "check 'a'", "unknown check type 'bogus'", "file_exists")

    def test_duplicate_id(self) -> None:
        body = 'id = "a"\ntitle = "A"\ntype = "git_clean"\n'
        self.assertError(check(body) + "\n[[check]]\nfix_hint = \"x\"\n" + body, "duplicate id")

    def test_missing_required_fields(self) -> None:
        self.assertError(check('title = "A"\ntype = "git_clean"\n'), "missing required field 'id'")
        self.assertError(check('id = "a"\ntype = "git_clean"\n'), "missing required field 'title'")
        self.assertError(check('id = "a"\ntitle = "A"\n'), "missing required field 'type'")

    def test_missing_and_unknown_parameters(self) -> None:
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "file_exists"\n'), "missing required parameter 'paths'")
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "file_exists"\npaths = ["x"]\npath = "typo"\n'), "unknown parameter 'path'")

    def test_bad_regex(self) -> None:
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "file_contains"\nfile = "README.md"\npattern = "(["\n'), "bad regex")
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "links_ok"\nignore = ["*bad"]\n'), "bad regex")

    def test_bad_timestamps(self) -> None:
        base = 'id = "a"\ntitle = "A"\ntype = "git_first_commit_after"\n'
        self.assertError(check(base + 'after = "yesterday"\n'), "bad timestamp")
        self.assertError(check(base + 'after = "2026-11-03T18:31:00"\n'), "needs a timezone")
        self.assertEqual(self.errors(check(base + 'after = "2026-11-03T18:31:00Z"\n')), [])
        self.assertEqual(self.errors(check(base + "after = 2026-11-03T18:31:00Z\n")), [])  # a native TOML datetime

    def test_bad_ids_and_types_of_values(self) -> None:
        self.assertError(check('id = "Bad_ID"\ntitle = "A"\ntype = "git_clean"\n'), "kebab-case")
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "git_clean"\nrequired = "yes"\n'), "'required' must be true or false")
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "max_file_mb"\nlimit_mb = -3\n'), "must be at least")
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "file_contains"\nfile = "a"\npattern = "x"\nflags = "z"\n'), "flag letters")
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "readme_headings"\nheadings = ["ok", "a|"]\n'), "empty alternative")

    def test_paths_must_stay_inside_the_project(self) -> None:
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "file_exists"\npaths = ["/etc/passwd"]\n'), "relative")
        self.assertError(check('id = "a"\ntitle = "A"\ntype = "readme_headings"\nfile = "../x.md"\nheadings = ["a"]\n'), "no '..'")

    def test_every_command_check_gets_a_warning(self) -> None:
        text = check('id = "a"\ntitle = "A"\ntype = "command"\ncmd = "make test"\n') + (
            '\n[[check]]\nfix_hint = "x"\nid = "b"\ntitle = "B"\ntype = "command"\ncmd = "npm test"\n'
        )
        warnings = [p for p in self.lint(text) if p.startswith("WARNING") and "runs code" in p]
        self.assertEqual(len(warnings), 2)
        self.assertIn("make test", warnings[0])
        self.assertIn("a person must approve it", warnings[0])
        self.assertEqual(self.errors(text), [])

    def test_meta_problems(self) -> None:
        self.assertError('[[check]]\nid = "a"\ntitle = "A"\ntype = "git_clean"\n', "missing required field 'name'", "missing required field 'version'")
        self.assertError('[meta]\nname = "x"\nversion = "one"\n[[check]]\nid="a"\ntitle="A"\ntype="git_clean"\n', "'version' must be a whole number")
        self.assertError('[meta]\nname = "x"\nversion = 1\nrubric = [["only a name"]]\n[[check]]\nid="a"\ntitle="A"\ntype="git_clean"\n', "'rubric'")
        self.assertIn("no 'source'", "\n".join(self.lint('[meta]\nname = "x"\nversion = 1\n[[check]]\nid="a"\ntitle="A"\ntype="git_clean"\nfix_hint="f"\n')))

    def test_syntax_errors_and_empty_rulesets(self) -> None:
        self.assertError("[meta\nname = ", "cannot parse")
        self.assertError(META, "no [[check]] entries")
        self.assertError("", "missing required field 'name'")

    def test_missing_file(self) -> None:
        _, problems = parse_ruleset(self.tmp / "absent.toml")
        self.assertIn("cannot read", str(problems[0]))

    def test_rubric_is_parsed_and_kept(self) -> None:
        text = META.replace("[meta]\n", '[meta]\nrubric = [["Use of Switch", 30], ["Docs", 20.5]]\n') + (
            '\n[[check]]\nfix_hint = "f"\nid = "a"\ntitle = "A"\ntype = "git_clean"\n'
        )
        path = self.tmp / "r.toml"
        path.write_text(text, encoding="utf-8")
        self.assertEqual(load_ruleset(path).rubric, [("Use of Switch", 30.0), ("Docs", 20.5)])

    def test_load_ruleset_raises_with_every_error(self) -> None:
        path = self.tmp / "bad.toml"
        path.write_text(check('id = "a"\ntype = "bogus"\n'), encoding="utf-8")
        with self.assertRaises(RulesetError) as caught:
            load_ruleset(path)
        self.assertGreaterEqual(len(caught.exception.problems), 2)


class ExtendsTests(RepoCase):
    def test_child_overrides_a_base_check_and_adds_new_ones(self) -> None:
        (self.tmp / "base.toml").write_text(
            META + '\n[[check]]\nid = "a"\ntitle = "A"\ntype = "git_clean"\nfix_hint = "f"\n'
            '\n[[check]]\nid = "b"\ntitle = "B"\ntype = "file_exists"\npaths = ["x"]\nfix_hint = "f"\n',
            encoding="utf-8",
        )
        (self.tmp / "child.toml").write_text(
            '[meta]\nname = "Child"\nversion = 2\nextends = "base.toml"\n'
            '\n[[check]]\nid = "b"\nrequired = false\n'
            '\n[[check]]\nid = "c"\ntitle = "C"\ntype = "git_clean"\nfix_hint = "f"\n',
            encoding="utf-8",
        )
        rules = load_ruleset(self.tmp / "child.toml")
        self.assertEqual((rules.name, rules.version, rules.source), ("Child", 2, "https://example.org/r"))
        self.assertEqual([c.id for c in rules.checks], ["a", "b", "c"])
        self.assertEqual([c.required for c in rules.checks], [True, False, True])
        self.assertEqual(rules.checks[1].params, {"paths": ["x"]})  # the base entry's params survive

    def test_missing_base_and_loops_are_errors(self) -> None:
        (self.tmp / "orphan.toml").write_text('[meta]\nname = "x"\nversion = 1\nextends = "nope.toml"\n', encoding="utf-8")
        _, problems = parse_ruleset(self.tmp / "orphan.toml")
        self.assertTrue(any("cannot read" in str(p) for p in problems))
        (self.tmp / "a.toml").write_text('[meta]\nname = "a"\nversion = 1\nextends = "b.toml"\n', encoding="utf-8")
        (self.tmp / "b.toml").write_text('[meta]\nname = "b"\nversion = 1\nextends = "a.toml"\n', encoding="utf-8")
        _, problems = parse_ruleset(self.tmp / "a.toml")
        self.assertTrue(any("too deep" in str(p) for p in problems))


class ShippedRulesetTests(unittest.TestCase):
    ALL = [*sorted(RULES.glob("*.toml")), EXAMPLES / "sample.rules.toml", *sorted(FIXTURES.glob("*.toml"))]

    def test_every_shipped_ruleset_lints_without_errors(self) -> None:
        for path in self.ALL:
            with self.subTest(ruleset=path.name):
                ruleset, problems = parse_ruleset(path)
                self.assertIsNotNone(ruleset, [str(p) for p in problems])
                self.assertEqual([p for p in problems if p.level == "ERROR"], [])

    def test_contest_ruleset_has_the_required_and_advisory_checks(self) -> None:
        rules = load_ruleset(RULES / "flip-the-switch.toml")
        required = [c.id for c in rules.checks if c.required]
        advisory = [c.id for c in rules.checks if not c.required]
        self.assertEqual(
            required,
            ["repo-public", "first-commit-after-start", "license", "readme-headings", "secret-scan", "no-env-file",
             "links-ok", "video-link-present", "git-clean", "template-present", "tests-pass"],
        )
        self.assertEqual(advisory, ["video-max-180s", "max-file-25mb", "no-todo-markers"])
        self.assertEqual((rules.name, rules.version), ("Flip the Switch Hackathon", 1))
        self.assertEqual(rules.source, "https://www.wemakedevs.org/hackathons/switch/rules")
        self.assertEqual(sum(weight for _, weight in rules.rubric), 100)
        by_id = {c.id: c for c in rules.checks}
        self.assertEqual(by_id["first-commit-after-start"].params["after"], "2026-11-03T18:31:00Z")
        self.assertTrue(by_id["secret-scan"].params["history"])
        self.assertEqual(by_id["video-max-180s"].params["max_seconds"], 180)
        self.assertEqual(by_id["tests-pass"].params["cmd"], "python3 -m unittest discover -s tests")

    def test_the_oss_ruleset_is_a_different_ruleset_on_the_same_engine(self) -> None:
        rules = load_ruleset(RULES / "oss-release.toml")
        self.assertEqual(len(rules.checks), 9)
        self.assertEqual(rules.name, "Open-source release checklist")
        headings = next(c for c in rules.checks if c.id == "readme-headings").params["headings"]
        self.assertEqual(headings, ["Install", "Usage", "Contributing"])
        self.assertTrue({c.type for c in rules.checks} <= set(CHECK_TYPES))

    def test_the_sample_ruleset_differs_from_the_contest_ruleset_only_in_repo_public(self) -> None:
        contest = load_ruleset(RULES / "flip-the-switch.toml")
        sample = load_ruleset(EXAMPLES / "sample.rules.toml")
        self.assertEqual([c.id for c in sample.checks], [c.id for c in contest.checks])
        differences = [(a.id, a.required, b.required) for a, b in zip(contest.checks, sample.checks)
                       if (a.required, a.params, a.type, a.title, a.fix_hint) != (b.required, b.params, b.type, b.title, b.fix_hint)]
        self.assertEqual(differences, [("repo-public", True, False)])
        self.assertEqual(sample.rubric, contest.rubric)


class LintCommandTests(RepoCase):
    def test_exit_codes_and_output(self) -> None:
        good = run_cli("lint", "--rules", str(RULES / "flip-the-switch.toml"))
        self.assertEqual(good[0], 0)
        self.assertEqual(good[1].count("WARNING"), 1)  # exactly one command check
        self.assertIn("tests-pass", good[1])
        bad = self.tmp / "bad.toml"
        bad.write_text(check('id = "a"\ntitle = "A"\ntype = "bogus"\n'), encoding="utf-8")
        code, out, _ = run_cli("lint", "--rules", str(bad))
        self.assertEqual(code, 1)
        self.assertEqual(len(out.strip().splitlines()), 1)  # one line per problem
        self.assertTrue(out.startswith("ERROR: check 'a':"))

    def test_clean_ruleset_prints_ok(self) -> None:
        code, out, _ = run_cli("lint", "--rules", str(FIXTURES / "tiny.toml"))
        self.assertEqual((code, out.startswith("OK:")), (0, True))


if __name__ == "__main__":
    unittest.main()
