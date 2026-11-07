"""The dry room (spec section 6.4): the whole table, every out-of-order call, a restart, two agents at once."""

from __future__ import annotations

import threading
import time
import unittest

from wf_helpers import PLAN_2, SPEC_2, FlowCase

# One valid call of every command the table can refuse.
CALLS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "new": (("new", "another goal", "--by", "asmit"), None),
    "spec submit": (("spec", "submit"), SPEC_2),
    "plan submit": (("plan", "submit"), PLAN_2),
    "change": (("change", "use blue", "--by", "asmit"), None),
    "build start": (("build", "start"), None),
    "run": (("run", "--suite"), None),
    "commit": (("commit", "--step", "1", "-m", "x", "--", "README.md"), None),
    "build done": (("build", "done"), None),
    "review": (("review", "approve", "--findings", "ok"), None),
    "test start": (("test", "start"), None),
    "test item": (("test", "item", "1", "PASS", "--evidence", "x"), None),
    "test done": (("test", "done"), None),
    "ship": (("ship",), None),
    "answer": (("answer", "--by", "asmit", "--quote", "ok"), None),
}

# What the table of spec section 6.2 allows at each point of the flow; every other call must be refused.
ALLOWED = {
    "spec": {"spec submit", "change"},
    "plan": {"plan submit", "change"},
    "build, not started": {"change", "build start"},
    "build": {"build start", "run", "commit", "build done"},
    "review": {"review"},
    "test, no clone": {"test start"},
    "test": {"test start", "test item"},
    "ship": {"ship"},
}


class DryRoomTests(FlowCase):
    def assert_only_allowed(self, point: str) -> None:
        for command, (argv, stdin) in CALLS.items():
            if command in ALLOWED[point]:
                continue
            before = self.raw_state()
            with self.subTest(at=point, command=command):
                code, text = self.run_wf(*argv, stdin=stdin)
                self.assertEqual(code, 2, f"{command} at {point}: {text}")
                self.assertEqual(self.raw_state(), before, f"{command} at {point} changed the state")

    def test_the_whole_table(self) -> None:
        self.start_task()
        self.assertEqual(self.task()["stage"], "spec")
        self.assert_only_allowed("spec")
        self.wf("spec", "submit", stdin=SPEC_2)
        self.assertEqual(self.task()["stage"], "plan")
        self.assert_only_allowed("plan")
        self.wf("plan", "submit", stdin=PLAN_2)
        self.assertEqual(self.task()["stage"], "build")
        self.assert_only_allowed("build, not started")
        self.wf("build", "start")
        self.assert_only_allowed("build")
        self.build_step(1)
        self.build_step(2)
        self.wf("build", "done")
        self.assertEqual(self.task()["stage"], "review")
        self.assert_only_allowed("review")
        self.wf("review", "approve", "--findings", "clean")
        self.assertEqual(self.task()["stage"], "test")
        self.assert_only_allowed("test, no clone")
        self.wf("test", "start")
        self.assert_only_allowed("test")
        self.report_items()
        self.wf("test", "done")
        self.assertEqual(self.task()["stage"], "ship")
        self.assert_only_allowed("ship")

    def test_a_reset_builder_continues_from_status(self) -> None:  # spec 6.4, second test
        self.to_build()
        self.wf("build", "start")
        self.build_step(1)
        self.write_test(2)
        self.wf("run", "--step", "2", "--red")
        self.wf("commit", "--step", "2", "--test", "-m", "file 2 exists", "--", "tests/2.sh")
        # the builder's session is reset here: it knows nothing and starts at line 1 of its instructions
        text = self.wf("status", "--for", "builder", speaker="builder")
        self.assertIn("Steps with a code commit: 1 of 2.", text)
        self.assertIn("Next for step 2: write the least code, then ./wf run --step 2 --green", text)
        self.write_code(2)
        self.wf("run", "--step", "2", "--green")
        self.wf("commit", "--step", "2", "-m", "add file 2", "--", "src/2.txt")
        self.wf("build", "done")
        self.assertEqual(self.task()["stage"], "review")

    def test_a_stop_during_a_slow_run_wins_and_the_run_is_not_recorded(self) -> None:  # Review Focus 3
        self.configure(suite=["sleep 2"])
        self.to_build()
        self.wf("build", "start")
        result: dict[str, tuple[int, str]] = {}
        worker = threading.Thread(target=lambda: result.setdefault("run", self.run_wf("run", "--suite")))
        worker.start()
        time.sleep(0.5)
        start = time.monotonic()
        self.wf("stop", "--by", "asmit")
        self.assertLess(time.monotonic() - start, 1.0)
        worker.join(timeout=30)
        code, text = result["run"]
        self.assertEqual(code, 2, text)
        self.assertIn("not recorded", text)
        self.assertIsNone(self.task()["build"]["suite"])


if __name__ == "__main__":
    unittest.main()
