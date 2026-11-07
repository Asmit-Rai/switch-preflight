"""wf core commands (plan Task 1): new, status, next, spec, plan, change, cancel, ask, answer, stop, resume, log, note."""

from __future__ import annotations

import json
import unittest

from helpers import commit_all, git, write
from wf_helpers import PLAN_2, SPEC_4, WfCase
from wf import packets, specplan


class NewTests(WfCase):
    def test_new_starts_a_task_and_puts_the_tree_on_main(self) -> None:
        self.git("switch", "-q", "-c", "elsewhere")
        text = self.wf("new", "add Ctrl+D to duplicate the selected shape", "--by", "asmit", speaker="manager")
        self.assertEqual(text, "T1 started: add Ctrl+D to duplicate the selected shape. Branch t1-ctrl-d-duplicate.\nNext: spec")
        task = self.task()
        self.assertEqual((task["id"], task["stage"], task["branch"], task["by"]), ("T1", "spec", "t1-ctrl-d-duplicate", "asmit"))
        self.assertEqual(task["base"], self.git("rev-parse", "main"))
        self.assertEqual(self.git("branch", "--show-current"), "main")
        goal = (self.project / "docs" / "tasks" / "T1" / "goal.md").read_text()
        self.assertIn("add Ctrl+D to duplicate the selected shape", goal)
        self.assertIn("asmit", goal)

    def test_a_mention_in_front_of_the_goal_is_dropped(self) -> None:
        self.assertTrue(self.wf("new", "@manager add a button", "--by", "asmit").startswith("T1 started: add a button."))
        self.assertTrue(self.wf("next").startswith('@specifier T1: write the spec for "add a button"'))

    def test_the_id_skips_numbers_already_used(self) -> None:
        self.git("branch", "t3-old-work")
        self.wf("new", "make it", "--by", "asmit")
        self.assertEqual(self.state()["active"], "T4")

    def test_a_second_goal_is_refused_while_a_task_is_active(self) -> None:
        self.start_task()
        before = self.raw_state()
        text = self.refused("new", "another thing", "--by", "asmit")
        self.assertIn("T1 is active", text)
        self.assertIn("cancel it or wait", text)
        self.assertEqual(self.raw_state(), before)

    def test_a_dirty_tree_is_refused_by_name(self) -> None:
        write(self.project, "notes.txt", "x")
        text = self.refused("new", "make it", "--by", "asmit")
        self.assertIn("notes.txt", text)
        self.assertFalse((self.project / ".workflow" / "state.json").exists())

    def test_an_empty_goal_or_name_is_refused(self) -> None:
        self.refused("new", "   ", "--by", "asmit")
        self.refused("new", "make it", "--by", " ")
        self.assertIn("over 2000 characters", self.refused("new", "x" * 2001, "--by", "asmit"))

    def remote_with_a_new_commit(self) -> str:
        git(self.tmp, "init", "-q", "--bare", "remote.git")
        self.git("remote", "add", "origin", str(self.tmp / "remote.git"))
        self.git("push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", "-b", "main", str(self.tmp / "remote.git"), "other")
        write(self.tmp / "other", "from-remote.txt", "x")
        sha = commit_all(self.tmp / "other", "a commit made elsewhere")
        git(self.tmp / "other", "push", "-q", "origin", "main")
        return sha

    def test_new_fast_forwards_main_from_the_remote(self) -> None:
        sha = self.remote_with_a_new_commit()
        self.start_task()
        self.assertEqual(self.git("rev-parse", "main"), sha)
        self.assertEqual(self.task()["base"], sha)

    def test_new_refuses_when_main_and_the_remote_diverged(self) -> None:
        self.remote_with_a_new_commit()
        write(self.project, "local.txt", "x")
        commit_all(self.project, "a local commit on main")
        text = self.refused("new", "make it", "--by", "asmit")
        self.assertIn("diverged", text)
        self.assertEqual(self.state()["active"], None)


class SpecAndPlanTests(WfCase):
    def test_spec_submit_writes_the_spec_and_moves_to_plan(self) -> None:
        self.start_task()
        text = self.wf("spec", "submit", stdin=SPEC_4, speaker="specifier")
        lines = text.splitlines()
        self.assertEqual(lines[0], "Spec T1 ready: 4 items (docs/tasks/T1/spec.md)")
        self.assertEqual(lines[1], "1. Ctrl+D with one shape selected adds a copy 10px right and down [unit]")
        self.assertIn("Assumed: offset 10px; copies keep colour and fill. Tell the manager if that is wrong.", lines)
        self.assertEqual(lines[-1], "Next: plan")
        task = self.task()
        self.assertEqual(task["stage"], "plan")
        written = (self.project / "docs" / "tasks" / "T1" / "spec.md").read_text()
        self.assertEqual(specplan.spec_sha(written), task["spec"]["sha256"])
        self.assertEqual([i["check"] for i in task["spec"]["items"]], ["unit", "unit", "unit", "browser"])

    def test_a_bad_spec_is_refused_and_changes_nothing(self) -> None:
        self.start_task()
        before = self.raw_state()
        text = self.refused("spec", "submit", stdin="1. no check here\nAssumed: nothing\n")
        self.assertIn("item 1 has no check type", text)
        self.assertEqual(self.raw_state(), before)

    def test_spec_from_a_file(self) -> None:
        self.start_task()
        path = self.tmp / "spec.txt"
        path.write_text(SPEC_4)
        self.wf("spec", "submit", "--file", str(path), speaker="specifier")
        self.assertEqual(self.task()["stage"], "plan")

    def test_plan_submit_moves_to_build(self) -> None:
        self.to_plan()
        text = self.wf("plan", "submit", stdin=PLAN_2, speaker="planner")
        lines = text.splitlines()
        self.assertEqual(lines[0], "Plan T1 ready: 2 steps for 2 items (docs/tasks/T1/plan.md)")
        self.assertEqual(lines[1], "1. first file (item 1, unit)")
        self.assertEqual(lines[-1], "Next: build")
        self.assertEqual(self.task()["stage"], "build")
        self.assertTrue((self.project / "docs" / "tasks" / "T1" / "plan.md").is_file())

    def test_a_plan_that_misses_an_item_is_refused(self) -> None:
        self.to_plan()
        text = self.refused("plan", "submit", stdin=PLAN_2.splitlines()[0] + "\n")
        self.assertIn("item 2 has no step", text)
        self.assertEqual(self.task()["stage"], "plan")

    def test_change_goes_back_to_spec_with_the_quote(self) -> None:
        self.to_build()
        text = self.wf("change", "use 20px", "--by", "asmit", speaker="manager")
        self.assertEqual(text, 'T1 goes back to spec: asmit asked "use 20px".\nNext: spec')
        task = self.task()
        self.assertEqual((task["stage"], task["spec"], task["plan"]), ("spec", None, None))
        self.assertEqual(task["quotes"][-1]["text"], "use 20px")
        packet = self.wf("next", speaker="manager")
        self.assertTrue(packet.startswith("@specifier T1: "))
        self.assertIn('change asked by asmit: "use 20px"', packet)


class LongAssumedLineTests(WfCase):
    def test_a_long_assumed_line_is_wrapped_so_the_change_hint_is_not_cut(self) -> None:
        # Found in review: "Assumed: <160 characters> Tell the manager if that is wrong." is 204 characters, so fit()
        # cut it in the middle of a word and the hint at its end was lost.
        self.start_task()
        assumed = ("the drawing is a board file the person opens with Import JSON, because agents cannot draw on the "
                   "board yet; the project is the app as built")
        self.assertTrue(135 <= len(assumed) <= 160, len(assumed))
        text = self.wf("spec", "submit", stdin=f"1. The first file exists [unit]\nAssumed: {assumed}\n", speaker="specifier")
        self.assertNotIn("...", text)
        self.assertIn(f"Assumed: {assumed} Tell the manager if that is wrong.", " ".join(text.split()))
        self.assertEqual(text.splitlines()[-1], "Next: plan")


class WaitingAndStoppedTests(WfCase):
    def test_a_question_blocks_the_stage_until_the_manager_records_the_answer(self) -> None:
        self.to_plan()
        text = self.wf("ask", "Faster at what: load, drawing, or export?", "--default", "drawing 100 shapes", speaker="planner")
        self.assertEqual(
            text,
            "WAITING ON YOU: Faster at what: load, drawing, or export? Default: drawing 100 shapes. "
            "Tell the manager ok, or answer in your words.",
        )
        before = self.raw_state()
        for argv, stdin in ((("plan", "submit"), PLAN_2), (("ask", "again?", "--default", "x"), None),
                            (("change", "x", "--by", "asmit"), None), (("spec", "submit"), SPEC_4)):
            with self.subTest(command=argv):
                self.assertIn("waiting on a person", self.refused(*argv, stdin=stdin))
        self.assertEqual(self.raw_state(), before)
        self.assertIn("WAITING ON YOU: Faster at what", self.wf("status", speaker="manager"))
        self.assertTrue(self.wf("next", speaker="planner").startswith("WAITING ON YOU:"))
        text = self.wf("answer", "--by", "asmit", "--quote", "drawing", speaker="manager")
        self.assertTrue(text.startswith('Answer recorded for T1 from asmit: "drawing".'), text)
        packet = self.wf("next", speaker="manager")
        self.assertTrue(packet.startswith("@planner T1: "))
        self.assertIn('answer from asmit: "drawing"', packet)
        self.wf("plan", "submit", stdin=PLAN_2, speaker="planner")

    def test_a_long_question_is_wrapped_so_the_default_and_the_reply_hint_are_not_cut(self) -> None:
        # Found in the first live run: one 330-character line was cut at 180 characters, in the middle of a word,
        # and the Default and "Tell the manager ok" at its end were lost.
        self.to_plan()
        question = ("Step 5's browser test can't fail in order: Import JSON already works and the file is complete after "
                    "step 4. Steps 5 and 6 also have no code to commit (no src change).")
        default = "run step 5's red first, then commit a note"
        text = self.wf("ask", question, "--default", default, speaker="planner")
        self.assertNotIn("...", text)
        self.assertTrue(text.startswith("WAITING ON YOU: Step 5's browser test"), text)
        self.assertGreater(len(text.splitlines()), 1, "a line this long is wrapped, not cut")
        self.assertEqual(" ".join(text.split()),
                         f"WAITING ON YOU: {question} Default: {default}. Tell the manager ok, or answer in your words.")
        for command in (("status",), ("next",)):
            with self.subTest(command=command):
                shown = self.wf(*command, speaker="manager")
                self.assertNotIn("...", shown)
                self.assertIn(f"Default: {default}. Tell the manager ok", " ".join(shown.split()))

    def test_answer_without_a_question_is_refused(self) -> None:
        self.to_plan()
        self.assertIn("no question is waiting", self.refused("answer", "--by", "asmit", "--quote", "ok"))

    def test_stop_and_resume(self) -> None:
        self.to_plan()
        text = self.wf("stop", "--by", "asmit", speaker="manager")
        self.assertTrue(text.startswith("T1 stopped at plan."), text)
        self.assertIn("stopped", self.refused("plan", "submit", stdin=PLAN_2))
        self.assertIn("already stopped", self.wf("stop", speaker="manager"))
        self.assertIn("Stopped by asmit", self.wf("status", speaker="manager"))
        packet = self.wf("resume", speaker="manager")
        self.assertTrue(packet.startswith("@planner T1: "), packet)
        self.wf("plan", "submit", stdin=PLAN_2, speaker="planner")

    def test_cancel_before_the_build_moves_the_records_out_of_the_tree(self) -> None:
        self.to_build()
        text = self.wf("cancel", "not needed", "--by", "asmit", speaker="manager")
        self.assertTrue(text.startswith('T1 cancelled: asmit said "not needed".'), text)
        self.assertIn(".workflow/tasks/T1/cancelled-records", text)
        self.assertTrue(text.endswith("Nothing needed from you."))
        self.assertFalse((self.project / "docs" / "tasks" / "T1").exists())
        self.assertTrue((self.project / ".workflow" / "tasks" / "T1" / "cancelled-records" / "plan.md").is_file())
        self.assertEqual(self.task("T1")["stage"], "cancelled")
        self.assertIsNone(self.state()["active"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue(self.wf("new", "the next thing", "--by", "asmit").startswith("T2 started"))

    def test_cancel_works_while_waiting_or_stopped(self) -> None:
        self.to_plan()
        self.wf("ask", "Which colour?", "--default", "blue")
        self.wf("stop", "--by", "asmit")
        self.wf("cancel", "drop it", "--by", "asmit")
        self.assertEqual(self.task("T1")["stage"], "cancelled")
        self.assertIn("no active task", self.refused("resume"))


class HandoffTests(WfCase):
    def test_every_packet_is_four_lines_with_the_next_alias_first(self) -> None:
        self.start_task()
        expected = [("manager", "@specifier T1: "), ("specifier", "@planner T1: "), ("planner", "@builder T1: build steps 1-2 on t1-two-files")]
        stdins = [None, "1. The first file exists [unit]\n2. The second file exists [unit]\nAssumed: nothing\n", PLAN_2]
        commands = [None, ("spec", "submit"), ("plan", "submit")]
        for (speaker, start), stdin, command in zip(expected, stdins, commands):
            if command:
                self.wf(*command, stdin=stdin, speaker=speaker)
            packet = self.wf("next", speaker=speaker)
            lines = packet.splitlines()
            with self.subTest(stage=self.task()["stage"]):
                self.assertEqual(len(lines), 4, packet)
                self.assertTrue(lines[0].startswith(start), lines[0])
                self.assertEqual([line.split(":")[0] for line in lines[1:]], ["See", "Do", "Prove"])
                self.assertLess(len(packet), 600)
                self.assertNotRegex(packet, r"@\w+\.")

    def test_long_goals_and_quotes_keep_the_packet_short(self) -> None:
        self.start_task(goal="make it " + "very " * 390 + "fast")  # 1962 characters, under the 2000 limit
        self.wf("change", "and " * 200 + "blue", "--by", "asmit")
        packet = self.wf("next", speaker="manager")
        self.assertLess(len(packet), 600)
        self.assertEqual(len(packet.splitlines()), 4)

    def test_status_for_an_agent_says_whose_turn_and_records_the_pickup(self) -> None:
        self.start_task()
        self.wf("next", speaker="manager")
        before = self.raw_state()
        text = self.wf("status", "--for", "planner", speaker="planner")
        self.assertTrue(text.startswith("Not your turn: T1 is at spec."), text)
        self.assertEqual(self.raw_state(), before)
        self.tick(9)
        self.assertIn("Hand-off to specifier posted 10:00, not picked up (9 min).", self.wf("status", speaker="manager"))
        text = self.wf("status", "--for", "specifier", speaker="specifier")
        self.assertTrue(text.startswith("Your turn: write the spec for T1"), text)
        self.assertEqual(self.task()["handoff"]["received_at"], "2026-11-05T10:09:00+05:30")
        self.assertIn("specifier picked up the hand-off at 10:09", self.wf("status", speaker="manager"))

    def test_the_manager_always_has_the_turn_while_a_task_runs(self) -> None:
        self.to_plan()
        self.wf("next", speaker="specifier")
        before = self.raw_state()
        text = self.wf("status", "--for", "manager", speaker="manager")
        self.assertTrue(text.startswith("Your turn: you answer people. The agents are working on T1."), text)
        self.assertIn("T1 at plan: add the two files", text)
        self.assertIn("stop, resume, change, cancel", text)
        self.assertEqual(self.raw_state(), before)  # the planner's hand-off is not marked as picked up

    def test_next_twice_counts_a_repost(self) -> None:
        self.start_task()
        self.wf("next")
        self.wf("next")
        self.assertEqual(self.task()["handoff"]["posted_count"], 2)

    def test_status_and_next_with_no_task(self) -> None:
        self.assertEqual(self.wf("status", speaker="manager"), "No task yet. Tell the manager what you want built.")
        self.assertTrue(self.wf("status", "--for", "builder", speaker="builder").startswith("Not your turn: no active task."))
        self.assertTrue(self.wf("status", "--for", "manager", speaker="manager").startswith("Your turn: no active task."))
        self.assertEqual(self.wf("next"), "Nothing to hand off: no task yet.")

    def test_an_unknown_alias_is_refused(self) -> None:
        self.assertIn("manager, specifier, planner, builder, reviewer, tester, shipper", self.refused("status", "--for", "boss"))

    def test_nothing_may_stand_before_the_command(self) -> None:  # found by the review: ./wf -- ship
        for argv in (("--", "ship"), ("--json", "ship"), ("--x", "y", "ship")):
            code, text = self.run_wf(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("Say the command first", text)

    def test_json_output(self) -> None:
        self.start_task()
        data = json.loads(self.wf("status", "--json"))
        self.assertEqual(data["code"], 0)
        self.assertEqual(data["task"]["id"], "T1")
        self.assertIn("T1 at spec", data["text"])


class LogAndNoteTests(WfCase):
    def test_log_lists_what_happened_in_plain_words(self) -> None:
        self.to_plan()
        self.refused("spec", "submit", stdin="1. x [unit]\nAssumed: nothing\n")
        text = self.wf("log", speaker="manager")
        lines = text.splitlines()
        self.assertRegex(lines[0], r"^T1 timeline \(\d+ events\):$")
        self.assertIn("10:00 new by asmit: ok", lines)
        self.assertIn("10:00 spec submit: ok", lines)
        self.assertTrue(any(line.startswith("10:00 spec submit: refused") for line in lines), lines)

    def test_note_summarises_the_task(self) -> None:
        self.to_plan()
        text = self.wf("note", speaker="manager")
        self.assertTrue(text.startswith("T1 closing note: add the two files"), text)
        self.assertIn("Now at plan.", text)


class WaitingLineWrapTests(unittest.TestCase):
    def test_the_loop_limit_line_is_wrapped_into_lines_the_chat_does_not_cut(self) -> None:
        last = "tester FAIL: the heading still says the old words " * 4
        task = {"id": "T1", "loops": 3, "waiting": {"kind": "loops", "question": last}}
        text = packets.waiting_line(task)
        self.assertTrue(text.startswith("WAITING ON YOU: T1 has looped 3 times"), text)
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 180, text)
        self.assertTrue(text.endswith("Default: continue. Tell the manager."), text)


if __name__ == "__main__":
    unittest.main()
