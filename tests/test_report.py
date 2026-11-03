"""Report formats: the table stays within 100 columns, the chat message within 1800 characters."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from preflight.model import Result, Ruleset
from preflight.report import CHAT_LIMIT, format_chat, format_json, format_table, verdict_line
from preflight.verdict import build_document

RULESET = Ruleset("Flip the Switch Hackathon", 1, "https://example.org", [], [], "r.toml")
LONG = "this detail is deliberately long, so that it has to wrap in the table and be clipped in the chat message "


def doc_for(results: list[Result], project: str = "/path/to/proj") -> dict:
    return build_document(RULESET, Path(project), "1a21df49955b2b19a0614032a4f73e8964395074", False, results,
                          "2026-11-04T09:12:00+05:30")


def row(n: int, status: str, required: bool = True, detail: str = "") -> Result:
    return Result(f"check-number-{n}", f"Check {n}", "file_exists", required, status, detail or LONG * 2, "hint")


class TableTests(unittest.TestCase):
    def test_layout_matches_the_brief(self) -> None:
        results = [
            Result("repo-public", "t", "git_remote_public", True, "PASS", "github.com/Asmit-Rai/x is public", ""),
            Result("license", "t", "file_exists", True, "FAIL", "no LICENSE file found", ""),
            Result("video-max-180s", "t", "video_max_seconds", False, "WARN", "video is 3:12 (192 s)", ""),
            Result("secret-scan", "t", "secret_scan", True, "SKIP", "offline", ""),
        ]
        lines = format_table(doc_for(results)).splitlines()
        self.assertEqual(lines[0], "PREFLIGHT  Flip the Switch Hackathon (ruleset v1)")
        self.assertEqual(lines[1], "project: /path/to/proj   commit: 1a21df4   run: 2026-11-04T09:12:00+05:30")
        self.assertRegex(lines[2], r"^\s+#  ID\s+REQ  STATUS  DETAIL$")
        self.assertRegex(lines[3], r"^\s+1  repo-public\s+yes  PASS    github\.com/Asmit-Rai/x is public$")
        self.assertRegex(lines[4], r"^\s+2  license\s+yes  FAIL    no LICENSE file found$")
        self.assertRegex(lines[5], r"^\s+3  video-max-180s\s+no   WARN    ")
        self.assertEqual(lines[-2], "SUMMARY: 1 pass, 1 fail, 1 warn, 1 skip")
        self.assertEqual(lines[-1], "VERDICT: NOT READY (blocking: license, secret-scan)")

    def test_every_line_but_the_last_fits_in_100_columns(self) -> None:
        results = [row(n, "FAIL") for n in range(12)]
        text = format_table(doc_for(results, project="/very/long/" + "folder/" * 20 + "project"))
        lines = text.splitlines()
        self.assertTrue(all(len(line) <= 100 for line in lines[:-1]), [len(l) for l in lines])
        self.assertTrue(lines[-1].startswith("VERDICT: NOT READY (blocking: check-number-0, "))

    def test_ready_verdict_line_is_exact(self) -> None:
        text = format_table(doc_for([Result("a", "t", "git_clean", True, "PASS", "ok", "")]))
        self.assertEqual(text.splitlines()[-1], "VERDICT: READY")

    def test_a_long_detail_wraps_under_its_column(self) -> None:
        text = format_table(doc_for([Result("a", "t", "git_clean", True, "FAIL", "word " * 60, "")]))
        continuation = [l for l in text.splitlines() if l.strip().startswith("word") and "FAIL" not in l]
        self.assertTrue(continuation)
        self.assertTrue(all(l.startswith(" " * 10) for l in continuation))


class ChatTests(unittest.TestCase):
    def test_a_small_report_is_complete_and_short(self) -> None:
        results = [
            Result("license", "t", "file_exists", True, "FAIL", "no LICENSE file found", ""),
            Result("git-clean", "t", "git_clean", True, "PASS", "clean", ""),
            Result("opt", "t", "git_clean", False, "WARN", "meh", ""),
        ]
        text = format_chat(doc_for(results))
        self.assertLess(len(text), 400)
        self.assertNotIn("(+", text)
        lines = text.splitlines()
        self.assertEqual(lines[0], "PREFLIGHT Flip the Switch Hackathon (v1) @ 1a21df4")
        self.assertEqual(lines[1], "FAIL license: no LICENSE file found")
        self.assertIn("WARN opt: meh", lines)
        self.assertIn("PASS (1): git-clean", lines)
        self.assertEqual(lines[-1], "VERDICT: NOT READY (blocking: license)")

    def test_thirty_failing_checks_still_fit_one_discord_message(self) -> None:
        text = format_chat(doc_for([row(n, "FAIL") for n in range(30)]))
        self.assertLessEqual(len(text), CHAT_LIMIT)
        self.assertRegex(text, r"\(\+\d+ more, run with --format table\)")
        lines = text.splitlines()
        self.assertTrue(lines[-1].startswith("VERDICT: NOT READY (blocking: check-number-0"))
        self.assertTrue(lines[-2].startswith("SUMMARY: 0 pass, 30 fail"))

    def test_the_more_line_counts_exactly_the_checks_left_out(self) -> None:
        doc = doc_for([row(n, "FAIL") for n in range(30)])
        text = format_chat(doc)
        shown = len(re.findall(r"^FAIL check-number-\d+:", text, re.M))
        hidden = int(re.search(r"\(\+(\d+) more", text).group(1))
        self.assertEqual(shown + hidden, 30)
        self.assertGreater(hidden, 0)

    def test_thirty_mixed_checks_and_huge_details_stay_within_the_limit(self) -> None:
        results = [row(n, ["FAIL", "SKIP", "PASS", "WARN"][n % 4], required=n % 3 != 0, detail="x" * 600) for n in range(30)]
        for limit in (CHAT_LIMIT, 900, 500):
            self.assertLessEqual(len(format_chat(doc_for(results), limit)), limit)

    def test_blocking_checks_are_listed_before_passes_and_the_verdict_stays_last(self) -> None:
        results = [Result(f"ok-{n}", "t", "git_clean", True, "PASS", "fine", "") for n in range(20)]
        results.append(Result("bad", "t", "git_clean", True, "FAIL", "broken", ""))
        lines = format_chat(doc_for(results)).splitlines()
        self.assertEqual(lines[1], "FAIL bad: broken")
        self.assertTrue(lines[2].startswith("PASS (20):"))
        self.assertEqual(lines[-1], "VERDICT: NOT READY (blocking: bad)")

    def test_the_pass_line_cuts_between_ids_and_counts_the_rest(self) -> None:
        results = [Result(f"a-fairly-long-check-id-{n}", "t", "git_clean", True, "PASS", "ok", "") for n in range(20)]
        results.append(Result("bad", "t", "git_clean", True, "FAIL", "broken", ""))
        pass_line = format_chat(doc_for(results)).splitlines()[2]
        self.assertLessEqual(len(pass_line), 150)
        self.assertRegex(pass_line, r"^PASS \(20\): a-fairly-long-check-id-0, .*check-id-\d+, \+\d+ more$")

    def test_ready(self) -> None:
        text = format_chat(doc_for([Result("a", "t", "git_clean", True, "PASS", "ok", "")]))
        self.assertEqual(text.splitlines()[-1], "VERDICT: READY")

    def test_a_long_blocking_list_is_cut_with_a_count(self) -> None:
        doc = doc_for([row(n, "FAIL") for n in range(60)])
        line = verdict_line(doc, 120)
        self.assertLessEqual(len(line), len("VERDICT: NOT READY (blocking: )") + 120)
        self.assertRegex(line, r", \+\d+ more\)$")
        self.assertNotIn("+", verdict_line(doc))  # the full line is never abbreviated


class JsonTests(unittest.TestCase):
    def test_json_round_trips(self) -> None:
        doc = doc_for([Result("a", "t", "git_clean", True, "PASS", "ok", "h")])
        self.assertEqual(json.loads(format_json(doc)), doc)


if __name__ == "__main__":
    unittest.main()
