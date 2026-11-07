"""wf/specplan.py: the grammar of the "Done means" items and of the plan steps (plan, pinned formats)."""

from __future__ import annotations

import unittest

import helpers  # noqa: F401
from wf import specplan
from wf.errors import Refused

SPEC = """Done means:
1. Ctrl+D with one shape selected adds a copy 10px right and down [unit]
2. Works with several shapes selected [unit]
3. One undo step [unit]
4. The copy is selected afterwards [browser]
Assumed: offset 10px; copies keep colour and fill.
"""

PLAN = """Steps:
1. Ctrl+D copies one shape | items 1 | unit | tests/dup.test.ts | npx vitest run tests/dup.test.ts
2. Several shapes and one undo step | items 2-3 | unit | tests/dup.test.ts | npx vitest run tests/dup.test.ts | tail -5
3. The copy is selected | item 4 | browser | e2e/dup.spec.ts | npx playwright test e2e/dup.spec.ts
4. It looks right | items 1, 4 | manual | - | open the app, draw a box, press Ctrl+D, see the copy
"""


class self_raises:  # a tiny helper so the refusal text can be asserted
    text = ""

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb):
        if kind is None:
            raise AssertionError("expected a refusal")
        if not issubclass(kind, Refused):
            return False
        self.text = str(exc)
        return True


class SpecTests(unittest.TestCase):
    def test_the_example_of_the_spec_parses(self) -> None:
        items, assumed = specplan.parse_spec(SPEC)
        self.assertEqual([i.n for i in items], [1, 2, 3, 4])
        self.assertEqual([i.check for i in items], ["unit", "unit", "unit", "browser"])
        self.assertEqual(items[0].text, "Ctrl+D with one shape selected adds a copy 10px right and down")
        self.assertEqual(assumed, "offset 10px; copies keep colour and fill.")

    def test_combined_checks_and_notes(self) -> None:
        items, _ = specplan.parse_spec(
            "1. Health is ok [api + Browser]\n2. Port 4399 [browser: the tester starts npm start first]\n"
            "3. Text has [brackets] inside [manual]\nAssumed: nothing\n"
        )
        self.assertEqual(items[0].check, "API + browser")
        self.assertEqual(items[1].check, "browser: the tester starts npm start first")
        self.assertEqual(items[2].text, "Text has [brackets] inside")

    def test_an_indented_line_continues_the_item_above(self) -> None:
        items, _ = specplan.parse_spec("1. a long item that\n   wraps here [unit]\nAssumed: nothing\n")
        self.assertEqual(items[0].text, "a long item that wraps here")

    def test_headings_and_blank_lines_are_ignored(self) -> None:
        items, _ = specplan.parse_spec("# Spec T7\n\nDone means:\n\n1. a [unit]\n\nAssumed: nothing\n")
        self.assertEqual(len(items), 1)

    def test_wrong_shapes_are_refused_with_the_line_number(self) -> None:
        cases = {
            "- a bullet item [unit]\nAssumed: nothing\n": "line 1",
            "1. no bracket here\nAssumed: nothing\n": "item 1 has no check type",
            "1. fast [fast]\nAssumed: nothing\n": "[fast] is not a check type",
            "1. a [unit]\n3. c [unit]\nAssumed: nothing\n": "numbered 1 to 2",
            "1. a [unit]\n": "no Assumed line",
            "1. a [unit]\nAssumed: x\nAssumed: y\n": "a second Assumed line",
            "Assumed: nothing\n": "no items",
            "".join(f"{n}. item {n} [unit]\n" for n in range(1, 32)) + "Assumed: nothing\n": "more than 30 items",
            "1. " + "x" * 301 + " [unit]\nAssumed: nothing\n": "over 300 characters",
            "1. a [unit]\nAssumed:\n": "Assumed line is empty",
        }
        for text, words in cases.items():
            with self.subTest(text=text[:40]):
                with self_raises() as box:
                    specplan.parse_spec(text)
                self.assertIn(words, box.text)
                self.assertIn("./wf spec submit", box.text)

    def test_render_ends_with_a_sha_that_checks_out(self) -> None:
        items, assumed = specplan.parse_spec(SPEC)
        text, sha = specplan.render_spec("T7", "add Ctrl+D", items, assumed)
        self.assertTrue(text.startswith("# T7 spec: add Ctrl+D\n\nDone means:\n1. Ctrl+D with one shape"))
        self.assertTrue(text.endswith(f"\nSHA-256: {sha}\n"))
        self.assertEqual(specplan.spec_sha(text), sha)
        self.assertIsNone(specplan.spec_sha(text.replace("10px", "20px")))
        again, sha2 = specplan.render_spec("T7", "add Ctrl+D", items, assumed)
        self.assertEqual((again, sha2), (text, sha))


class PlanTests(unittest.TestCase):
    def test_a_plan_parses(self) -> None:
        steps = specplan.parse_plan(PLAN, [1, 2, 3, 4])
        self.assertEqual([s.k for s in steps], [1, 2, 3, 4])
        self.assertEqual(steps[1].items, (2, 3))
        self.assertEqual(steps[1].command, "npx vitest run tests/dup.test.ts | tail -5")
        self.assertEqual(steps[2].type, "browser")
        self.assertEqual(steps[3].type, "manual")
        self.assertEqual(steps[3].file, "-")
        self.assertEqual(steps[3].items, (1, 4))

    def test_wrong_plans_are_refused(self) -> None:
        cases = {
            "1. a | items 1 | unit | t.sh | sh t.sh\n": "item 2 has no step",
            "1. a | items 1, 2, 9 | unit | t.sh | sh t.sh\n": "item 9, which is not in the spec",
            "1. a | items 1, 2 | unit | t.sh |\n": "step 1 has no command",
            "1. a | items 1, 2 | unit | - | sh t.sh\n": "step 1 has no test file",
            "1. a | items 1, 2 | slow | t.sh | sh t.sh\n": "unit, API, browser or manual",
            "1. a | items one | unit | t.sh | sh t.sh\n": "items must be numbers",
            "1. a | items 1, 2 | unit\n": "5 parts",
            "* a | items 1, 2 | unit | t.sh | sh t.sh\n": "not a step",
            "2. a | items 1, 2 | unit | t.sh | sh t.sh\n": "numbered 1 to 1",
            "1. a | items 1, 2 | manual | - |\n": "the words to follow",
        }
        for text, words in cases.items():
            with self.subTest(text=text[:40]):
                with self_raises() as box:
                    specplan.parse_plan(text, [1, 2])
                self.assertIn(words, box.text)
                self.assertIn("./wf plan submit", box.text)

    def test_render_plan(self) -> None:
        steps = specplan.parse_plan(PLAN, [1, 2, 3, 4])
        text = specplan.render_plan("T7", "add Ctrl+D", steps)
        self.assertTrue(text.startswith("# T7 plan: add Ctrl+D\n\nSteps (what | items | type | test file | command):\n"))
        self.assertIn("2. Several shapes and one undo step | items 2, 3 | unit | tests/dup.test.ts | npx vitest run", text)
        self.assertEqual([s.k for s in specplan.parse_plan(text, [1, 2, 3, 4])], [1, 2, 3, 4])  # it reads back


if __name__ == "__main__":
    unittest.main()
