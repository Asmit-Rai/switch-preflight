"""wf review and test commands, loops and a moved HEAD (plan Task 2)."""

from __future__ import annotations

import unittest
from pathlib import Path

from helpers import commit_all, write
from wf_helpers import FlowCase


class ReviewTests(FlowCase):
    def test_approve_moves_to_test(self) -> None:
        self.to_review()
        head = self.git("rev-parse", "HEAD")
        text = self.wf("review", "approve", "--findings", "Clean: tests check behaviour.", speaker="reviewer")
        self.assertTrue(text.startswith(f"REVIEW T1: APPROVE at {head[:7]}"), text)
        self.assertTrue(text.endswith("Next: test"))
        task = self.task()
        self.assertEqual((task["stage"], task["review"]["verdict"], task["review"]["head"]), ("test", "APPROVE", head))

    def test_changes_need_findings_and_loop_back_to_build(self) -> None:
        self.to_review()
        self.assertIn("say what to change", self.refused("review", "changes", stdin=""))
        text = self.wf("review", "changes", stdin="1. tests/2.sh:1 checks the wrong file: check src/2.txt\n", speaker="reviewer")
        self.assertIn("CHANGES NEEDED", text)
        self.assertIn("(loop 1 of 3)", text)
        self.assertTrue(text.endswith("Next: build (fix 1)"), text)
        task = self.task()
        self.assertEqual((task["stage"], task["loops"], task["fix_round"]), ("build", 1, 1))
        packet = self.wf("next", speaker="reviewer")
        self.assertTrue(packet.startswith(f"@builder T1: fix 1 on {task['branch']} (review: CHANGES NEEDED at "), packet)
        self.assertIn("tests/2.sh:1 checks the wrong file", packet)

    def test_a_fix_round_is_built_test_first_and_reviewed_again(self) -> None:
        self.to_review()
        self.wf("review", "changes", "--findings", "1. src/2.txt says nothing useful: say file 2 is done")
        self.assertIn("this is fix round 1", self.refused("commit", "--fix", "2", "--test", "-m", "x", "--", "README.md"))
        write(self.project, "tests/f1.sh", "grep -q done src/2.txt\n")
        self.wf("run", "--fix", "1", "--red", "sh tests/f1.sh", speaker="builder")
        self.wf("commit", "--fix", "1", "--test", "-m", "file 2 says done", "--", "tests/f1.sh")
        write(self.project, "src/2.txt", "file 2 done\n")
        self.wf("run", "--fix", "1", "--green", "sh tests/f1.sh")
        self.wf("commit", "--fix", "1", "-m", "say done", "--", "src/2.txt")
        self.wf("build", "done", speaker="builder")
        self.assertEqual(self.task()["stage"], "review")
        self.assertEqual(self.git("log", "-2", "--format=%s").splitlines(), ["fix 1: say done", "fix 1 test: file 2 says done"])

    def test_a_fix_round_with_no_fix_is_refused(self) -> None:  # found by the review
        self.to_review()
        self.wf("review", "changes", "--findings", "1. word it better")
        text = self.refused("build", "done")
        self.assertIn("fix 1 has no test commit", text)
        self.assertIn("fix 1 has no fix commit", text)

    def test_a_fix_commit_without_its_test_commit_is_refused(self) -> None:
        self.to_review()
        self.wf("review", "changes", "--findings", "1. word it better")
        write(self.project, "src/2.txt", "better\n")
        self.assertIn("no test commit for fix 1", self.refused("commit", "--fix", "1", "-m", "better", "--", "src/2.txt"))


class LoopLimitTests(FlowCase):
    def setUp(self) -> None:
        super().setUp()
        self.configure(max_loops=1)
        self.to_review()
        text = self.wf("review", "changes", "--findings", "1. still wrong", speaker="reviewer")
        self.assertIn("WAITING ON YOU: T1 has looped 1 times", text)
        self.assertIn("Reply continue (one more round) or cancel. Default: continue.", text)

    def test_continue_starts_the_count_again(self) -> None:
        self.assertIn("waiting on a person", self.refused("run", "--suite"))
        self.assertIn("continue or cancel", self.refused("answer", "--by", "asmit", "--quote", "maybe later"))
        text = self.wf("answer", "--by", "asmit", "--quote", "continue", speaker="manager")
        self.assertIn("starts again at 0", text)
        task = self.task()
        self.assertEqual((task["stage"], task["loops"], task["waiting"]), ("build", 0, None))
        self.assertTrue(self.wf("next", speaker="manager").startswith("@builder T1: fix 1 on "))

    def test_cancel_ends_the_task_and_keeps_the_branch(self) -> None:
        branch = self.task()["branch"]
        text = self.wf("answer", "--by", "asmit", "--quote", "cancel it", speaker="manager")
        self.assertTrue(text.startswith('T1 cancelled: asmit said "cancel it".'), text)
        self.assertEqual(self.task("T1")["stage"], "cancelled")
        self.assertIn(branch, self.git("branch", "--list", branch))
        self.assertTrue(self.wf("new", "the next thing", "--by", "asmit").startswith("T2 started"))
        self.assertEqual(self.git("branch", "--show-current"), "main")


class CancelWithLeftoversTests(FlowCase):  # found by the review
    def setUp(self) -> None:
        super().setUp()
        self.to_build()
        self.wf("build", "start")

    def assert_cancelled_clean(self) -> None:
        text = self.wf("cancel", "stop it", "--by", "asmit", speaker="manager")
        self.assertIn("kept in the git stash as", text)
        self.assertEqual(self.task("T1")["stage"], "cancelled")
        self.assertTrue(self.task("T1")["cancel_stash"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("stash", "list").count("wf T1: uncommitted work at cancel"), 1)
        self.assertTrue(self.wf("new", "the next thing", "--by", "asmit").startswith("T2 started"))

    def test_an_untracked_file_only(self) -> None:
        self.write_test(1)
        self.assert_cancelled_clean()

    def test_a_modified_tracked_file(self) -> None:
        write(self.project, "README.md", "# proj, changed\n")
        self.assert_cancelled_clean()

    def test_a_name_with_spaces_and_a_star(self) -> None:
        write(self.project, "notes/a b*.txt", "x")
        self.assert_cancelled_clean()


class HeadMovedTests(FlowCase):  # Review Focus 1
    def test_a_commit_after_build_done_is_caught_and_build_done_runs_again(self) -> None:
        self.to_review()
        old = self.git("rev-parse", "HEAD")
        write(self.project, "src/2.txt", "file 2, again\n")
        new = commit_all(self.project, "step 2: a raw commit that got past the hook")
        text = self.refused("review", "approve", "--findings", "ok")
        self.assertIn(f"HEAD moved since build done (was {old[:7]}, now {new[:7]})", text)
        self.wf("build", "done", speaker="builder")
        task = self.task()
        self.assertEqual((task["stage"], task["head"]), ("review", new))
        self.assertIn("slip", self.wf("log"))
        self.wf("review", "approve", "--findings", "ok")

    def test_a_foreign_commit_after_build_done_cannot_be_built_over(self) -> None:
        self.to_review()
        write(self.project, "junk.txt", "x")
        commit_all(self.project, "quick fix")
        self.assertIn('"quick fix" is not a wf commit', self.refused("build", "done"))

    def test_test_and_ship_refuse_too(self) -> None:
        self.to_test()
        write(self.project, "src/1.txt", "moved\n")
        commit_all(self.project, "step 1: moved")
        self.assertIn("HEAD moved since build done", self.refused("test", "start"))

    def test_build_done_at_review_without_a_move_is_refused(self) -> None:
        self.to_review()
        self.assertIn("Not now: T1 is at review", self.refused("build", "done"))


class TestStageTests(FlowCase):
    def test_start_makes_a_clean_clone_at_head_and_installs(self) -> None:
        self.configure(install="echo installed > installed.txt")
        self.to_test()
        text = self.wf("test", "start", speaker="tester")
        clone = Path(self.task()["tests"]["clone"])
        self.assertTrue(str(clone).startswith(str(self.tmp / "clones" / "wf")), clone)
        self.assertIn(str(clone), text)
        self.assertIn("Install: echo installed > installed.txt passed (exit 0).", text)
        self.assertTrue((clone / "installed.txt").is_file())
        self.assertEqual((clone / "src" / "2.txt").read_text(), "file 2\n")
        self.assertFalse((self.project / "installed.txt").exists())

    def test_items_are_checked_and_all_pass_moves_to_ship(self) -> None:
        self.to_test()
        self.assertIn("no clean clone yet", self.refused("test", "item", "1", "PASS", "--evidence", "x"))
        self.wf("test", "start")
        self.assertIn("there is no item 9", self.refused("test", "item", "9", "PASS", "--evidence", "x"))
        self.assertIn("PASS or FAIL", self.refused("test", "item", "1", "maybe", "--evidence", "x"))
        self.assertIn("evidence", self.refused("test", "item", "1", "PASS", "--evidence", " "))
        text = self.wf("test", "item", "1", "pass", "--evidence", "sh tests/1.sh: exit 0", speaker="tester")
        self.assertEqual(text, "Item 1: PASS recorded (1 of 2 reported).\nNext: ./wf test item 2, then ./wf test done")
        self.assertIn("items 2 of T1 have no result yet", self.refused("test", "done"))
        self.wf("test", "item", "2", "PASS", "--evidence", "sh tests/2.sh: exit 0")
        text = self.wf("test", "done", speaker="tester")
        head = self.task()["head"]
        self.assertTrue(text.startswith(f"Test T1: 2 of 2 PASS at {head[:7]}"), text)
        self.assertTrue(text.endswith("Next: ship"))
        report = (self.project / ".workflow" / "tasks" / "T1" / "test-report.md").read_text()
        self.assertIn("2. PASS: The second file exists [unit]", report)
        self.assertEqual(self.task()["stage"], "ship")

    def test_a_fail_loops_back_to_build(self) -> None:
        self.to_test()
        self.wf("test", "start")
        self.report_items({2: "FAIL"})
        text = self.wf("test", "done", speaker="tester")
        self.assertIn("Test T1: 1 of 2 PASS", text)
        self.assertIn("FAIL item 2: sh tests/2.sh: exit 1", text)
        self.assertTrue(text.endswith("Next: build (fix 1)"), text)
        packet = self.wf("next", speaker="tester")
        self.assertIn("(test: item 2 FAIL at ", packet.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
