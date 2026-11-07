"""wf build commands (plan Task 2): build start, run, commit, build done."""

from __future__ import annotations

import time
import unittest

from helpers import commit_all, write
from wf_helpers import FlowCase


class BuildStartTests(FlowCase):
    def test_build_start_makes_the_branch_and_commits_the_records(self) -> None:
        self.to_build()
        text = self.wf("build", "start", speaker="builder")
        task = self.task()
        self.assertTrue(text.startswith(f"T1 build started on {task['branch']}: first commit "), text)
        self.assertEqual(self.git("branch", "--show-current"), task["branch"])
        self.assertEqual(self.git("log", "-1", "--format=%s"), "T1: spec and plan")
        self.assertEqual(self.git("show", "--name-only", "--format=", "HEAD").splitlines(),
                         ["docs/tasks/T1/goal.md", "docs/tasks/T1/plan.md", "docs/tasks/T1/spec.md"])
        self.assertEqual(self.git("rev-parse", "HEAD~1"), task["base"])
        self.assertTrue(task["build"]["started"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_repeat_says_already_started_and_puts_the_folder_back_on_the_branch(self) -> None:
        self.to_build()
        self.wf("build", "start")
        self.git("switch", "-q", "main")
        text = self.wf("build", "start", speaker="builder")
        self.assertIn("already started", text)
        self.assertEqual(self.git("branch", "--show-current"), self.task()["branch"])

    def test_a_dirty_tree_is_refused_but_the_records_are_not_dirt(self) -> None:
        self.to_build()
        write(self.project, "stray.txt", "x")
        self.assertIn("stray.txt", self.refused("build", "start"))
        self.assertFalse(self.task()["build"]["started"])

    def test_a_spec_changed_by_hand_is_refused(self) -> None:
        self.to_build()
        path = self.project / "docs" / "tasks" / "T1" / "spec.md"
        path.write_text(path.read_text().replace("first file", "third file"))
        self.assertIn("spec.md changed", self.refused("build", "start"))


class RunTests(FlowCase):
    def setUp(self) -> None:
        super().setUp()
        self.to_build()
        self.wf("build", "start")

    def test_red_keeps_the_failing_output_and_refuses_a_pass(self) -> None:
        self.write_test(1)
        text = self.wf("run", "--step", "1", "--red", speaker="builder")
        self.assertTrue(text.startswith("Step 1 RED: exit 1 at "), text)
        evidence = self.project / ".workflow" / "tasks" / "T1" / "evidence"
        self.assertIn("exit: 1", (evidence / "step-1-red.txt").read_text())
        self.write_code(1)
        text = self.refused("run", "--step", "1", "--red")
        self.assertIn("the command passed (exit 0)", text)
        self.assertTrue((evidence / "step-1-red-attempt-1.txt").is_file())
        self.assertEqual(self.task()["build"]["evidence"]["1"]["red"]["exit"], 1)

    def test_green_keeps_the_passing_output_and_refuses_a_failure(self) -> None:
        self.write_test(1)
        text = self.refused("run", "--step", "1", "--green")
        self.assertIn("still failing (exit 1)", text)
        self.write_code(1)
        self.assertTrue(self.wf("run", "--step", "1", "--green").startswith("Step 1 GREEN: exit 0"))
        self.assertIn("green", self.task()["build"]["evidence"]["1"])

    def test_the_plan_holds_the_command(self) -> None:
        self.assertIn("command is in the plan", self.refused("run", "--step", "1", "--red", "true"))
        self.assertIn("there is no step 9", self.refused("run", "--step", "9", "--red"))
        self.assertIn("--red or --green", self.refused("run", "--step", "1"))
        self.assertIn("there is no fix round", self.refused("run", "--fix", "1", "--red", "true"))

    def test_the_suite_records_its_commit_and_whether_the_tree_was_clean(self) -> None:
        text = self.wf("run", "--suite", speaker="builder")
        self.assertTrue(text.startswith("Suite green at "), text)
        suite = self.task()["build"]["suite"]
        self.assertEqual((suite["sha"], suite["clean"]), (self.git("rev-parse", "HEAD"), True))
        write(self.project, "loose.txt", "x")
        self.assertIn("not clean", self.wf("run", "--suite"))
        self.assertFalse(self.task()["build"]["suite"]["clean"])

    def test_a_failing_suite_is_refused_and_not_recorded(self) -> None:
        self.configure(suite=["true", "sh -c 'echo broken; exit 3'"])
        text = self.refused("run", "--suite")
        self.assertIn("exit 3", text)
        self.assertIsNone(self.task()["build"]["suite"])


class TimeoutTests(FlowCase):
    def test_a_command_that_hangs_is_stopped_and_refused(self) -> None:  # Review Focus 5
        self.configure(run_timeout=1)
        self.start_task()
        self.wf("spec", "submit", stdin="1. it waits [unit]\nAssumed: nothing\n")
        self.wf("plan", "submit", stdin="1. wait | items 1 | unit | tests/1.sh | sleep 30 & sleep 30\n")
        self.wf("build", "start")
        start = time.monotonic()
        text = self.refused("run", "--step", "1", "--red")
        self.assertLess(time.monotonic() - start, 15)
        self.assertIn("did not finish in 1s", text)
        self.assertNotIn("1", self.task()["build"]["evidence"])


class CommitTests(FlowCase):
    def setUp(self) -> None:
        super().setUp()
        self.to_build()
        self.wf("build", "start")

    def test_bad_files_and_messages_are_refused(self) -> None:
        self.write_test(1)
        self.wf("run", "--step", "1", "--red")
        write(self.project, "node_modules/x.js", "x")
        write(self.project, "dist/app.js", "x")
        cases = {
            ("node_modules/x.js",): "node_modules",
            ("dist/app.js",): "build output",
            ("../x",): "outside the project",
            (str(self.tmp / "elsewhere.txt"),): "outside the project",
            ("tests",): "is a folder",
            ("nothere.txt",): "does not exist",
            (".workflow/state.json",): "wf's own",
        }
        for files, words in cases.items():
            with self.subTest(files=files):
                self.assertIn(words, self.refused("commit", "--step", "1", "--test", "-m", "x", "--", *files))
        self.assertIn("one line", self.refused("commit", "--step", "1", "--test", "-m", "a\nb", "--", "tests/1.sh"))
        self.assertIn("Say what", self.refused("commit", "--step", "1", "--test", "-m", "  ", "--", "tests/1.sh"))

    def test_the_order_of_red_test_commit_green_code_commit_is_code(self) -> None:
        self.write_test(1)
        self.assertIn("no red run for step 1", self.refused("commit", "--step", "1", "--test", "-m", "x", "--", "tests/1.sh"))
        self.wf("run", "--step", "1", "--red")
        self.write_code(1)
        self.assertIn("no test commit for step 1", self.refused("commit", "--step", "1", "-m", "x", "--", "src/1.txt"))
        text = self.wf("commit", "--step", "1", "--test", "-m", "file 1 exists", "--", "tests/1.sh", speaker="builder")
        self.assertTrue(text.startswith("step 1 test: file 1 exists -> "), text)
        self.assertIn("no green run for step 1", self.refused("commit", "--step", "1", "-m", "x", "--", "src/1.txt"))
        self.wf("run", "--step", "1", "--green")
        self.wf("commit", "--step", "1", "-m", "add file 1", "--", "src/1.txt")
        self.assertEqual(self.git("log", "-2", "--format=%s").splitlines(), ["step 1: add file 1", "step 1 test: file 1 exists"])

    def test_only_the_named_files_are_committed(self) -> None:
        self.write_test(1)
        write(self.project, "other.txt", "not named")
        self.wf("run", "--step", "1", "--red")
        self.wf("commit", "--step", "1", "--test", "-m", "file 1 exists", "--", "tests/1.sh")
        self.assertEqual(self.git("show", "--name-only", "--format=", "HEAD"), "tests/1.sh")
        self.assertIn("?? other.txt", self.git("status", "--porcelain"))

    def test_nothing_to_commit_is_refused(self) -> None:
        self.write_test(1)
        self.wf("run", "--step", "1", "--red")
        self.wf("commit", "--step", "1", "--test", "-m", "file 1 exists", "--", "tests/1.sh")
        self.assertIn("nothing changed", self.refused("commit", "--step", "1", "--test", "-m", "again", "--", "tests/1.sh"))


class ManualStepTests(FlowCase):
    def test_a_manual_step_is_committed_without_a_test_and_build_done_accepts_it(self) -> None:
        self.start_task()
        self.wf("spec", "submit", stdin="1. The file exists [unit]\n2. It reads well [manual]\nAssumed: nothing\n")
        self.wf("plan", "submit", stdin="1. file | items 1 | unit | tests/1.sh | sh tests/1.sh\n"
                                         "2. wording | items 2 | manual | - | read src/1.txt and check the words\n")
        self.wf("build", "start")
        self.build_step(1)
        self.assertIn("step 2 is manual", self.refused("run", "--step", "2", "--red"))
        write(self.project, "src/1.txt", "file 1, worded well\n")
        self.assertIn("step 2 is manual", self.refused("commit", "--step", "2", "--test", "-m", "x", "--", "src/1.txt"))
        self.wf("commit", "--step", "2", "-m", "better words", "--", "src/1.txt")
        self.wf("build", "done", speaker="builder")
        self.assertEqual(self.task()["stage"], "review")

    def test_a_manual_step_can_commit_the_note_of_what_the_builder_saw(self) -> None:
        # The builder skill says: a manual step commits docs/tasks/T<n>/checks.md.
        self.start_task()
        self.wf("spec", "submit", stdin="1. The file exists [unit]\n2. It reads well [manual]\nAssumed: nothing\n")
        self.wf("plan", "submit", stdin="1. file | items 1 | unit | tests/1.sh | sh tests/1.sh\n"
                                         "2. wording | items 2 | manual | - | read src/1.txt and check the words\n")
        self.wf("build", "start")
        self.build_step(1)
        write(self.project, "docs/tasks/T1/checks.md", "step 2: I read src/1.txt. The words are fine.\n")
        self.wf("commit", "--step", "2", "-m", "read the words", "--", "docs/tasks/T1/checks.md")
        self.wf("build", "done", speaker="builder")
        self.assertEqual(self.task()["stage"], "review")
        self.assertIn("docs/tasks/T1/checks.md", self.git("show", "--name-only", "--format=", "HEAD"))


class BuildDoneTests(FlowCase):
    def setUp(self) -> None:
        super().setUp()
        self.to_build()
        self.wf("build", "start")

    def test_build_done_records_head_and_moves_to_review(self) -> None:
        self.build_step(1)
        self.build_step(2)
        text = self.wf("build", "done", speaker="builder")
        task = self.task()
        self.assertTrue(text.startswith(f"T1 built: 2 of 2 steps, 5 commits on {task['branch']}, suite green at "), text)
        self.assertTrue(text.endswith("Next: review"))
        self.assertEqual((task["stage"], task["head"]), ("review", self.git("rev-parse", "HEAD")))

    def test_build_done_runs_the_suite_when_the_last_green_one_is_not_at_head(self) -> None:
        self.build_step(1)
        self.build_step(2)
        write(self.project, "src/2.txt", "file 2, changed\n")
        self.wf("commit", "--step", "2", "-m", "word it better", "--", "src/2.txt")
        text = self.wf("build", "done")
        self.assertIn("wf ran the suite now", text)
        self.assertEqual(self.task()["build"]["suite"]["sha"], self.git("rev-parse", "HEAD"))

    def test_missing_steps_and_foreign_commits_are_named(self) -> None:
        self.build_step(1)
        text = self.refused("build", "done")
        self.assertIn("step 2 has no code commit", text)
        self.assertIn("step 2 has no red run", text)
        write(self.project, "junk.txt", "x")
        commit_all(self.project, "wip")
        self.assertIn('"wip" is not a wf commit', self.refused("build", "done"))

    def test_a_dirty_tree_is_refused(self) -> None:
        self.build_step(1)
        self.build_step(2)
        write(self.project, "loose.txt", "x")
        self.assertIn("loose.txt", self.refused("build", "done"))


if __name__ == "__main__":
    unittest.main()


class DigestTests(unittest.TestCase):
    VITEST_RED = """
 RUN  v4.1.12 /p

 ❯ tests/double.test.ts (0 test)

 FAIL  tests/double.test.ts [ tests/double.test.ts ]
Error: Failed to resolve import "../src/double" from "tests/double.test.ts". Does the file exist?
  Plugin: vite:import-analysis
 ❯ TransformPluginContext._formatLog node_modules/vite/dist/node/chunks/node.js:8803:39

 Test Files  1 failed (1)
      Tests  no tests
   Start at  10:40:51
   Duration  1.52s (transform 28ms, setup 38ms, import 0ms, tests 0ms, environment 1.11s)
"""

    def test_a_failed_run_shows_why_first_and_never_the_timing_lines(self) -> None:
        from wf import evidence

        lines = evidence.digest(self.VITEST_RED, failed=True)
        self.assertEqual(lines[0], 'Error: Failed to resolve import "../src/double" from "tests/double.test.ts". Does the file exist?')
        self.assertIn("Test Files  1 failed (1)", lines)
        self.assertFalse([line for line in lines if "Start at" in line or "Duration" in line])

    def test_a_passing_run_shows_its_counts_and_marks_become_words(self) -> None:
        from wf import evidence

        lines = evidence.digest(" ✓ tests/a.test.ts (2 tests) 3ms\n Test Files  1 passed (1)\n      Tests  2 passed (2)\n")
        self.assertEqual(lines, ["Test Files  1 passed (1)", "Tests  2 passed (2)"])
        self.assertIn("PASS", evidence.digest("✓ one\nall good")[0] + evidence.digest("✓ one\nall good")[-1])
