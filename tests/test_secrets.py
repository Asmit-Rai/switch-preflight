"""The secret scanner: patterns, masking, history, allowlist.

Every fake credential is assembled at runtime from short pieces, so this file never contains
a whole token-shaped string (GitHub push protection would reject it).
"""

from __future__ import annotations

import unittest

from helpers import AFTER, RepoCase, commit_all, git, write
from preflight.checks.secrets import PATTERNS, entropy, find_secrets, looks_random, mask, redact

RANDOM36 = "A1b2C3d4E5f6G7h8I9" + "j0K1l2M3n4O5p6Q7r8"


def fakes() -> dict[str, str]:
    """One fake credential per pattern, keyed by the pattern name the scanner reports."""
    return {
        "AWS access key id": "AKIA" + "IOSFODNN7EXAMPLE",
        "GitHub token": "gh" + "p_" + RANDOM36,
        "GitHub fine-grained token": "github" + "_pat_" + "11ABCDEFG0" + "abcdefghijklmnop",
        "Slack token": "xox" + "b-" + "1234567890-" + "abcdefghijkl",
        "Discord bot token": "M" + "TIzNDU2Nzg5MDEyMzQ1Njc4" + "." + "GabcDE" + "." + "abcdefghijklmnopqrstuvwxyz1",
        "private key (PEM header)": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "sk- API key": "sk" + "-proj-" + "abcdefghijklmnopqrstuvwx",
        "Stripe live key": "sk_" + "live_" + "abcdefghijklmnop1234",
    }


class PatternTests(unittest.TestCase):
    def test_every_pattern_finds_its_fake_and_masks_it(self) -> None:
        for name, secret in fakes().items():
            with self.subTest(pattern=name):
                hits = find_secrets(f"value = {secret}  # trailing")
                self.assertEqual([h.pattern for h in hits], [name])
                self.assertLessEqual(len(hits[0].masked) - 4, 4)  # at most 4 characters of the value
                self.assertNotIn(secret[4:], hits[0].masked)

    def test_all_eight_patterns_are_covered_by_a_fake(self) -> None:
        self.assertEqual({p.name for p in PATTERNS}, set(fakes()))

    def test_assignments_with_a_random_looking_value_are_found(self) -> None:
        for line in (
            f'SECRET_KEY = "{RANDOM36}"',
            f"api_token: {RANDOM36}",
            f'"password": "{RANDOM36}"',
            f"DB_PASSWORD={RANDOM36}",
        ):
            with self.subTest(line=line[:20]):
                hits = find_secrets(line)
                self.assertEqual([h.pattern for h in hits], ["secret assignment"])
                self.assertEqual(hits[0].masked, "****")

    def test_ordinary_lines_and_placeholders_are_not_findings(self) -> None:
        for line in (
            'API_KEY = "your_api_key_goes_here_1234"',
            'PASSWORD = os.environ["DB_PASSWORD"]',
            "token = tokenizer.encode_plus_something_long",
            "ghp_short",
            "AKIA is the prefix of an AWS access key id",
            "def get_secret_from_the_environment_variables():",
            "SECRET_KEY = 'changeme-changeme-1234567890'",
            "",
        ):
            with self.subTest(line=line[:30]):
                self.assertEqual(find_secrets(line), [])

    def test_obvious_placeholders_in_docs_and_env_examples_are_not_findings(self) -> None:
        for line in (
            "GITHUB_TOKEN=" + "gh" + "p_" + "x" * 36,
            "AWS_ACCESS_KEY_ID=" + "AKIA" + "X" * 16,
            "OPENAI_API_KEY=" + "sk" + "-" + "x" * 30,
            "SLACK_TOKEN=" + "xox" + "b-" + "0" * 12 + "-" + "0" * 12,
        ):
            with self.subTest(line=line[:18]):
                self.assertEqual(find_secrets(line), [])

    def test_a_precise_pattern_wins_over_the_loose_rule_on_the_same_line(self) -> None:
        hits = find_secrets(f"GITHUB_TOKEN={fakes()['GitHub token']}")
        self.assertEqual([h.pattern for h in hits], ["GitHub token"])

    def test_redact_masks_known_patterns_inside_text(self) -> None:
        token = fakes()["GitHub token"]
        text = redact(f"leaked {token} and {fakes()['AWS access key id']}")
        self.assertNotIn(token, text)
        self.assertIn("ghp_****", text)
        self.assertIn("AKIA****", text)

    def test_helpers(self) -> None:
        self.assertEqual(mask("abcdefgh"), "abcd****")
        self.assertEqual(mask("abcdefgh", show_prefix=False), "****")
        self.assertGreater(entropy("abcdefghij"), entropy("aaaaaaaaaa"))
        self.assertTrue(looks_random(RANDOM36))
        self.assertFalse(looks_random("a" * 30))


class ScanTests(RepoCase):
    def scan(self, repo, **params):
        return self.check(repo, "secret_scan", **params)

    def test_clean_repo_passes(self) -> None:
        repo = self.repo({"app.py": "print('hello world')\n", "README.md": "# hi\n"})
        out = self.scan(repo, history=True)
        self.assertStatus(out, "PASS", "no secrets in 2 tracked file(s) and 1 commit(s) of history")

    def test_a_tracked_secret_is_found_with_file_and_line_and_is_masked(self) -> None:
        token = fakes()["GitHub token"]
        repo = self.repo({"app.py": "x = 1\n", ".env": f"# local\nGITHUB_TOKEN={token}\n"})
        out = self.scan(repo)
        self.assertStatus(out, "FAIL", ".env:2", "GitHub token", "ghp_****")
        self.assertNotIn(token, out.detail)
        self.assertNotIn(RANDOM36, out.detail)

    def test_a_secret_in_history_is_found_even_after_it_was_removed(self) -> None:
        token = fakes()["AWS access key id"]
        repo = self.repo({"config.txt": f"key={token}\n"})
        write(repo, "config.txt", "key=removed\n")
        sha = commit_all(repo, "remove the key", AFTER)
        self.assertStatus(self.scan(repo, history=False), "PASS")  # gone from the working tree
        out = self.scan(repo, history=True)
        self.assertStatus(out, "FAIL", "config.txt:1", "AWS access key id", "in history", "AKIA****")
        self.assertNotIn(token, out.detail)
        self.assertNotIn(sha[:7], out.detail)  # it was added by the first commit, not by this one

    def test_history_names_the_commit_that_added_it(self) -> None:
        repo = self.repo({"a.txt": "fine\n"})
        write(repo, "b.txt", "line one\n" + f"token = {fakes()['Slack token']}\n")
        sha = commit_all(repo, "oops", AFTER)
        write(repo, "b.txt", "line one\ntoken = removed\n")
        commit_all(repo, "clean up", AFTER)
        out = self.scan(repo, history=True)
        self.assertStatus(out, "FAIL", "b.txt:2", "Slack token", f"in history {sha[:7]}")

    def test_a_secret_still_in_the_tree_is_reported_from_the_tree(self) -> None:
        repo = self.repo({"b.txt": f"token = {fakes()['Slack token']}\n"})
        out = self.scan(repo, history=True)
        self.assertStatus(out, "FAIL", "b.txt:1", "Slack token")
        self.assertNotIn("in history", out.detail)

    def test_the_same_secret_is_reported_once_across_tree_and_history(self) -> None:
        repo = self.repo({"a.txt": f"{fakes()['GitHub token']}\n"})
        out = self.scan(repo, history=True)
        self.assertIn("1 secret(s) found", out.detail)

    def test_other_branches_are_part_of_history(self) -> None:
        repo = self.repo({"a.txt": "fine\n"})
        git(repo, "checkout", "-q", "-b", "side")
        write(repo, "side.txt", f"{fakes()['Stripe live key']}\n")
        commit_all(repo, "side work", AFTER)
        git(repo, "checkout", "-q", "main")
        self.assertStatus(self.scan(repo, history=True), "FAIL", "side.txt:1", "Stripe live key")

    def test_untracked_ignored_and_binary_files_are_not_scanned(self) -> None:
        repo = self.repo({".gitignore": ".env\n", "blob.bin": b"\0" + fakes()["GitHub token"].encode()})
        write(repo, ".env", f"GITHUB_TOKEN={fakes()['GitHub token']}\n")
        out = self.scan(repo, history=False)
        self.assertStatus(out, "PASS")

    def test_pem_header_is_found_without_printing_anything_of_it(self) -> None:
        repo = self.repo({"key.txt": fakes()["private key (PEM header)"] + "\nabc\n"})
        out = self.scan(repo)
        self.assertStatus(out, "FAIL", "key.txt:1", "private key (PEM header)", "(****)")


class AllowlistTests(RepoCase):
    def files(self) -> dict[str, str]:
        token = fakes()["GitHub token"]
        return {"docs/example.md": f"intro\nexample: {token}\n", "app.py": "x = 1\n"}

    def test_path_and_line_entry_is_honoured_and_counted(self) -> None:
        repo = self.repo({**self.files(), ".preflight-allow": "# accepted by the Captain\ndocs/example.md:2\n"})
        out = self.check(repo, "secret_scan", history=True)
        self.assertStatus(out, "PASS", "1 finding(s) allowed via .preflight-allow")

    def test_a_wrong_line_number_does_not_allow_it(self) -> None:
        repo = self.repo({**self.files(), ".preflight-allow": "docs/example.md:1\n"})
        self.assertStatus(self.check(repo, "secret_scan"), "FAIL", "docs/example.md:2")

    def test_whole_path_glob_and_regex_entries(self) -> None:
        repo = self.repo({**self.files(), ".preflight-allow": "docs/*\n"})
        self.assertStatus(self.check(repo, "secret_scan", history=True), "PASS", "allowed")
        repo2 = self.repo({**self.files(), ".preflight-allow": "re:^example: \n"}, name="second")
        self.assertStatus(self.check(repo2, "secret_scan", history=True), "PASS", "allowed")

    def test_findings_outside_the_allowlist_still_fail_and_the_count_is_reported(self) -> None:
        files = {**self.files(), "other.py": f"k = '{fakes()['AWS access key id']}'\n", ".preflight-allow": "docs/example.md:2\n"}
        repo = self.repo(files)
        out = self.check(repo, "secret_scan")
        self.assertStatus(out, "FAIL", "other.py:1", "1 finding(s) allowed")

    def test_an_invalid_regex_line_is_ignored_and_reported(self) -> None:
        repo = self.repo({**self.files(), ".preflight-allow": "re:([unclosed\n"})
        out = self.check(repo, "secret_scan")
        self.assertStatus(out, "FAIL", "1 invalid line(s) in .preflight-allow ignored")


if __name__ == "__main__":
    unittest.main()
