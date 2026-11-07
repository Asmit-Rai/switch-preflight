"""wf/state.py: the state file is versioned, locked and written atomically (spec section 6.3)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401
from wf import state as wfstate
from wf.errors import WfError


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / ".workflow"
        self.store = wfstate.Store(self.dir)

    def test_a_missing_file_is_an_empty_state(self) -> None:
        self.assertEqual(self.store.load(), {"version": 1, "active": None, "tasks": {}})

    def test_a_transaction_saves_and_a_noop_writes_nothing(self) -> None:
        with self.store.transaction() as st:
            st["active"] = "T1"
        self.assertEqual(json.loads((self.dir / "state.json").read_text())["active"], "T1")
        before = (self.dir / "state.json").stat().st_mtime_ns
        time.sleep(0.01)
        with self.store.transaction():
            pass
        self.assertEqual((self.dir / "state.json").stat().st_mtime_ns, before)
        self.assertFalse((self.dir / "state.json.tmp").exists())

    def test_a_newer_version_asks_for_a_kit_update(self) -> None:
        self.dir.mkdir(parents=True)
        (self.dir / "state.json").write_text('{"version": 2, "active": null, "tasks": {}}')
        with self.assertRaises(WfError) as box:
            self.store.load()
        self.assertIn("update the kit", str(box.exception))

    def test_a_damaged_file_is_reported_and_never_overwritten(self) -> None:
        self.dir.mkdir(parents=True)
        for text in ("{not json", "[]", '{"active": null}'):
            (self.dir / "state.json").write_text(text)
            with self.assertRaises(WfError) as box:
                with self.store.transaction() as st:
                    st["active"] = "T9"
            self.assertIn("damaged", str(box.exception))
            self.assertEqual((self.dir / "state.json").read_text(), text)

    def test_a_crash_inside_a_transaction_keeps_the_old_file(self) -> None:
        with self.store.transaction() as st:
            st["active"] = "T1"
        old = (self.dir / "state.json").read_bytes()
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as st:
                st["active"] = "T2"
                raise RuntimeError("crash")
        self.assertEqual((self.dir / "state.json").read_bytes(), old)

    def test_a_second_writer_waits_for_the_lock(self) -> None:
        self.dir.mkdir(parents=True)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, sys, time\n"
             "f = open(sys.argv[1], 'a+')\n"
             "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
             "print('locked', flush=True)\n"
             "time.sleep(0.6)\n",
             str(self.dir / "lock")],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        start = time.monotonic()
        with self.store.transaction() as st:
            st["active"] = "T3"
        self.assertGreater(time.monotonic() - start, 0.3)
        self.assertEqual(self.store.load()["active"], "T3")
        holder.stdout.close()

    def test_a_lock_held_too_long_is_an_error_not_a_hang(self) -> None:
        self.dir.mkdir(parents=True)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, sys, time\n"
             "f = open(sys.argv[1], 'a+')\n"
             "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
             "print('locked', flush=True)\n"
             "time.sleep(3)\n",
             str(self.dir / "lock")],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        with self.assertRaises(WfError) as box:
            with self.store.transaction(timeout=0.3):
                pass
        self.assertIn("Try again", str(box.exception))
        holder.stdout.close()

    def test_the_timeline_appends_and_skips_a_bad_line(self) -> None:
        self.store.append_timeline("T1", {"command": "new", "result": "ok"})
        with open(self.dir / "tasks" / "T1" / "timeline.jsonl", "a") as fh:
            fh.write("not json\n")
        self.store.append_timeline("T1", {"command": "next", "result": "ok"})
        self.assertEqual([e["command"] for e in self.store.read_timeline("T1")], ["new", "next"])
        self.assertEqual(self.store.read_timeline("T9"), [])


if __name__ == "__main__":
    unittest.main()
