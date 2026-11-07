"""wf/chat.py: the house style every chat message from wf keeps (spec section 11)."""

from __future__ import annotations

import unittest

import helpers  # noqa: F401  (puts the repo root on sys.path)
from wf import chat


class ProblemsTests(unittest.TestCase):
    def test_a_plain_message_has_no_problems(self) -> None:
        self.assertEqual(chat.problems("T7 started: add a button. Branch t7-button.\nNext: spec"), [])

    def test_each_rule_is_checked(self) -> None:
        cases = {
            "over 1500": "a" * 1501,
            "line 1 is over 180": "a" * 181,
            "table row": "| item | result |",
            "JSON": '{"stage": "spec"}',
            "chat app": "posted on Discord",
            "3 of 4": "3/4 PASS",
            "check mark": "✓ item 1",
        }
        for words, text in cases.items():
            with self.subTest(rule=words):
                found = " ".join(chat.problems(text))
                self.assertIn(words, found)

    def test_slack_and_teams_are_named_too(self) -> None:
        for name in ("Slack", "Teams"):
            self.assertTrue(chat.problems(f"works in {name}"), name)

    def test_paths_and_times_are_not_counts(self) -> None:
        self.assertEqual(chat.problems("See docs/tasks/T7/spec.md at 12:03 on 2026-11-05"), [])

    def test_the_speaker_never_names_itself(self) -> None:
        self.assertTrue(chat.problems("@builder T7: build steps 1-4", speaker="builder"))
        self.assertEqual(chat.problems("@reviewer T7: review t7-x", speaker="builder"), [])
        self.assertEqual(chat.problems("@builders is another word", speaker="builder"), [])


class FitTests(unittest.TestCase):
    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(chat.fit("one\ntwo"), "one\ntwo")

    def test_a_long_text_keeps_its_first_and_last_line(self) -> None:
        text = "First line\n" + "\n".join("x" * 120 for _ in range(30)) + "\nNext: spec"
        out = chat.fit(text)
        self.assertTrue(out.startswith("First line\n"))
        self.assertTrue(out.endswith("\nNext: spec"))
        self.assertLessEqual(len(out), chat.MAX_CHARS)
        self.assertEqual(chat.problems(out), [])

    def test_a_long_line_is_cut_with_dots(self) -> None:
        out = chat.fit("b" * 400)
        self.assertEqual(len(out), chat.MAX_LINE)
        self.assertTrue(out.endswith("..."))

    def test_wrapped_breaks_a_long_line_at_spaces_and_loses_nothing(self) -> None:
        text = " ".join(f"word{n}" for n in range(80))
        out = chat.wrapped(text)
        lines = out.splitlines()
        self.assertGreater(len(lines), 1)
        self.assertLessEqual(max(len(line) for line in lines), chat.MAX_LINE - 2)
        self.assertEqual(" ".join(out.split()), text)
        self.assertEqual(chat.fit(out), out, "nothing is left for fit() to cut")

    def test_wrapped_leaves_a_short_line_alone(self) -> None:
        self.assertEqual(chat.wrapped("one short line"), "one short line")

    def test_bounded_lists_what_fits_and_says_how_many_more(self) -> None:
        lines = [f"{i}. item number {i} " + "y" * 60 for i in range(1, 41)]
        out = chat.bounded(lines, 400, "docs/tasks/T7/spec.md")
        self.assertLessEqual(len("\n".join(out)), 460)
        self.assertRegex(out[-1], r"^\.\.\. and \d+ more in docs/tasks/T7/spec\.md$")
        self.assertEqual(chat.bounded(lines[:2], 400, "x"), lines[:2])


if __name__ == "__main__":
    unittest.main()
