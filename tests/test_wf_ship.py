"""wf ship and verify (plan Task 3): the gate, the local merge, the push, the record."""

from __future__ import annotations

import json
import unittest

from helpers import git, write
from wf_helpers import FlowCase
from wf.cmd_ship import github_slug

RULES = """[meta]
name = "wf test rules"
version = 1
source = "https://example.org/wf-test"

[[check]]
id = "file"
title = "The file is there"
type = "file_exists"
paths = ["{path}"]
fix_hint = "add it"
"""


class ShipTests(FlowCase):
    def add_remote(self) -> None:
        git(self.tmp, "init", "-q", "--bare", "remote.git")
        self.git("remote", "add", "origin", str(self.tmp / "remote.git"))
        self.git("push", "-q", "origin", "main")

    def test_without_a_remote_ship_merges_into_main(self) -> None:
        self.to_ship()
        task = self.task()
        head, branch = task["head"], task["branch"]
        text = self.wf("ship", speaker="shipper")
        self.assertTrue(text.startswith(f"T1 shipped: merged {branch} into main as "), text)
        self.assertTrue(text.endswith("Next: manager posts the closing note"))
        done = self.task("T1")
        self.assertEqual((done["stage"], done["ship"]["how"], self.state()["active"]), ("done", "merge", None))
        self.assertEqual(self.git("log", "-1", "--format=%s", "main"), f"T1: merge {branch} (add the two files)")
        self.assertEqual(self.git("rev-parse", "main^2"), head)
        self.assertFalse((self.project / ".workflow" / "ship-token").exists())
        verify = self.wf("verify", "T1", speaker="manager")
        self.assertIn(f"Shipped at {head[:7]}: merged into main as", verify)
        self.assertIn("Tests: 2 of 2 PASS", verify)
        packet = self.wf("next", speaker="shipper")
        self.assertTrue(packet.startswith(f"@manager T1 shipped at {head[:7]}"), packet)
        self.assertIn(f"Shipped at {head[:7]}: merged as ", self.wf("note", speaker="manager"))
        self.assertTrue(self.wf("new", "the next thing", "--by", "asmit").startswith("T2 started"))

    def test_with_a_remote_ship_pushes_the_branch_and_keeps_main(self) -> None:
        self.add_remote()
        self.to_ship()
        task = self.task()
        text = self.wf("ship", speaker="shipper")
        self.assertIn(f"T1 shipped: pushed {task['branch']} at {task['head'][:7]}", text)
        self.assertEqual(git(self.tmp / "remote.git", "rev-parse", task["branch"]), task["head"])
        self.assertEqual(git(self.tmp / "remote.git", "rev-parse", "main"), task["base"])
        self.assertEqual(self.task("T1")["ship"]["how"], "push")
        self.assertFalse((self.project / ".workflow" / "ship-token").exists())
        body = (self.project / ".workflow" / "tasks" / "T1" / "evidence" / "pr-body.md").read_text()
        for words in ("### Done means", "1. The first file exists [unit]", "### Test report", "1. PASS: The first file exists",
                      "### Proof chain", "Gate open."):
            self.assertIn(words, body)

    def edit_state(self, change) -> None:
        path = self.project / ".workflow" / "state.json"
        state = json.loads(path.read_text())
        change(state["tasks"]["T1"])
        path.write_text(json.dumps(state))

    def test_the_gate_closes_for_each_single_miss_and_nothing_leaves(self) -> None:
        self.to_ship()
        main = self.git("rev-parse", "main")
        misses = {
            "the tree is not clean": lambda: write(self.project, "loose.txt", "x"),
            "no review APPROVE": lambda: self.edit_state(lambda t: t.update(review=None)),
            "item 2 not PASS": lambda: self.edit_state(lambda t: t["tests"]["items"]["2"].update(result="FAIL")),
            "no green suite": lambda: self.edit_state(lambda t: t["build"].update(suite=None)),
            "step 1 has no red run": lambda: self.edit_state(lambda t: t["build"]["evidence"]["1"].pop("red")),
        }
        for words, make_it_wrong in misses.items():
            with self.subTest(miss=words):
                before = (self.project / ".workflow" / "state.json").read_bytes()
                make_it_wrong()
                text = self.wf("ship", code=3)
                self.assertIn(words, text)
                self.assertIn("Nothing left the machine.", text)
                self.assertEqual(self.git("rev-parse", "main"), main)
                self.assertEqual(self.task()["stage"], "ship")
                self.assertIn(words, self.wf("verify", code=3))
                (self.project / ".workflow" / "state.json").write_bytes(before)
                (self.project / "loose.txt").unlink(missing_ok=True)
        self.wf("ship")

    def test_preflight_runs_inside_the_gate(self) -> None:
        rules = self.tmp / "rules.toml"
        rules.write_text(RULES.replace("{path}", "MISSING.md"))
        self.configure(rules=str(rules))
        self.to_ship()
        text = self.wf("ship", code=3)
        self.assertIn("Preflight is NOT READY", text)
        self.assertFalse((self.project / ".preflight").exists())
        rules.write_text(RULES.replace("{path}", "README.md"))
        self.assertIn("Preflight READY", self.wf("status", "--for", "shipper", speaker="shipper"))
        self.wf("ship")
        self.assertEqual(self.task("T1")["ship"]["preflight"], "READY")
        self.assertIn("Preflight: READY", self.wf("verify", "T1"))

    def test_status_for_the_shipper_prints_the_ready_line(self) -> None:
        self.to_ship()
        text = self.wf("status", "--for", "shipper", speaker="shipper")
        head = self.task()["head"]
        self.assertIn(f"T1 is ready to ship: review APPROVE at {head[:7]}, 2 of 2 items PASS, Preflight not configured.", text)
        self.assertTrue(text.endswith('WAITING ON YOU: a permission card for "./wf ship" follows. Allow once to ship it.'))

    def test_a_typed_confirmation_is_recorded(self) -> None:
        self.to_ship()
        self.assertIn("go together", self.refused("ship", "--confirmed-by", "asmit"))
        self.wf("ship", "--confirmed-by", "asmit", "--quote", "ship it")
        record = self.task("T1")["ship"]
        self.assertEqual((record["confirmed_by"], record["quote"]), ("asmit", "ship it"))

    def test_verify_before_ship_shows_where_the_chain_stops(self) -> None:
        self.to_review()
        text = self.wf("verify", code=3)
        self.assertIn("Review: none yet", text)
        self.assertIn("Gate closed: T1 is at review, not at ship", text)


class SlugTests(unittest.TestCase):
    def test_github_urls(self) -> None:
        for url in ("https://github.com/team-axa/sketchroom.git", "https://github.com/team-axa/sketchroom",
                    "git@github.com:team-axa/sketchroom.git", "ssh://git@github.com/team-axa/sketchroom.git"):
            self.assertEqual(github_slug(url), "team-axa/sketchroom", url)
        for url in ("/tmp/remote.git", "https://gitlab.com/a/b.git", "", None):
            self.assertIsNone(github_slug(url), url)


if __name__ == "__main__":
    unittest.main()
