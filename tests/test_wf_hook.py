"""The wf policy of the approval hook (hooks/risky_git.py --policy wf).

In a wf build room only `./wf commit` commits and only `./wf ship` pushes or merges. Under this policy
everything the default mode asks about is refused, git work done around wf is refused with the ./wf
command to use instead, and `./wf ship` alone becomes a permission card.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "tests"))  # also when run as `python3 -m unittest tests.test_wf_hook`
import risky_git  # noqa: E402
from test_risky_git_hook import MUST_ASK, MUST_ASK_GH, MUST_ASK_TARGET, run_hook  # noqa: E402

# Git work done around wf: refused, with the ./wf command to use instead.
DENY_WITH_WF_ADVICE = [
    "git switch main",
    "git checkout -b x",
    "git -C /tmp/c switch --detach a1",
    "git add README.md",
    "git commit -m x",
    "git -C p commit -q -m x",
    "git stash",
    "git stash list",
    "git merge --ff-only x",
    "git pull --ff-only",
    "git cherry-pick a1",
    "cd p && git add -A",
    "bash -c 'git commit -m x'",
]

DENY_PUBLISH = ["npm publish", "pnpm publish --access public"]

# The one command that becomes a permission card, however it is written.
ASK_SHIP = [
    "./wf ship",
    "cd /p && ./wf ship",
    "python3 -m wf ship",
    "/x/wf ship --confirmed-by a --quote \"ship it\"",
    "wf ship",
    # the ship must ask however it is written: quoted, or with an option between wf and ship
    './wf "ship"',
    "./wf 'ship'",
    "./wf --project /p ship",
    "/abs/kit/wf --project=/p ship --confirmed-by asmit --quote yes",
    # found by the review: an end-of-options marker, a quoted launcher path, the module run by its file
    "./wf -- ship",
    '"/abs/path/wf" ship',
    "python3 -m wf.__main__ ship",
    "python3 -m wf.cli ship",
]

# The everyday work of a wf room: no card and no refusal.
SILENT = [
    './wf commit --step 1 -m "ship it" -- a.txt',
    "./wf commit --step 1 -m 'ship the button' -- a.txt",
    './wf ask "ship now or wait?" --default "wait"',
    "./wf ask 'the command you needed, in plain words' --default 'what you do instead, without that command'",
    "./wf ask 'I needed rm -rf .playwright-mcp to clean up' --default 'a person removes it'",
    './wf ask "I needed rm -rf .playwright-mcp to clean up" --default "a person removes it"',
    "./wf status --for shipper",
    "./wf skill shipper",
    "./wf next",
    "./wf commit --step 1 -m \"git add x\" -- a.txt",
    "./wf commit --step 1 --test -m \"check git commit wording\" -- tests/a.test.ts",
    "git merge-base a b",
    "git status --short",
    "git diff main..HEAD -- tests",
    "git log --oneline -5",
    "git show HEAD:README.md",
    "git rev-parse HEAD",
    "git branch --show-current",
    "git fetch origin",
    "git clone --branch t1-x /p /tmp/c",
    "npm test",
    "npm ci",
    "npx vitest run tests/a.test.ts",
    "cat > notes.md <<'EOF'\ngit commit\ngit switch main\nEOF",
    "ls -la",
    # Agents with no Write tool submit specs and plans through a heredoc: its words are text, not commands.
    "./wf spec submit <<'EOF'\n1. rm -rf build and git reset --hard are refused [unit]\nAssumed: nothing\nEOF",
    "./wf plan submit <<'EOF'\n1. a | items 1 | unit | t.sh | git commit -m x && git push\nEOF",
]

SWITCH_REASON = (
    "switches branches: ./wf build start puts the builder on the task branch, and the tester works in the clean "
    "clone from ./wf test start"
)
ADD_REASON = 'stages files outside wf: use ./wf commit --step <k> -m "<what>" -- <files>'
COMMIT_REASON = "makes a commit outside wf: use ./wf commit"
STASH_REASON = "stashes work, which makes a dirty tree look clean: commit with ./wf commit instead"
MERGE_REASON = "merges branches: only ./wf ship merges, after a person clicks Allow once"
SHIP_REASON = "ships the task (./wf ship): it pushes the branch and opens the pull request, or merges into main"


def bash(command: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


class WfDecisionTests(unittest.TestCase):
    def test_everything_the_default_mode_asks_about_is_denied(self) -> None:
        answers = {c: risky_git.wf_decision(c) for c in MUST_ASK + MUST_ASK_TARGET + MUST_ASK_GH}
        not_denied = {c: a for c, a in answers.items() if a is None or a[0] != "deny"}
        self.assertEqual(not_denied, {}, "these would run (or only ask) in a wf room")

    def test_the_default_reason_comes_first_when_both_rules_match(self) -> None:
        # `git checkout -f` also switches branches and `--amend` also commits: the sharper reason is kept
        for command in ("git checkout -f main", "git commit --amend -m x", "git stash drop"):
            self.assertEqual(risky_git.wf_decision(command), ("deny", risky_git.risky_reason(command)), command)

    def test_git_work_around_wf_is_denied_with_the_wf_command_to_use(self) -> None:
        answers = {c: risky_git.wf_decision(c) for c in DENY_WITH_WF_ADVICE}
        wrong = {c: a for c, a in answers.items() if a is None or a[0] != "deny" or "./wf" not in a[1]}
        self.assertEqual(wrong, {})

    def test_each_kind_of_git_work_has_its_own_reason(self) -> None:
        cases = {
            "git switch main": SWITCH_REASON,
            "git checkout -b x": SWITCH_REASON,
            "git add README.md": ADD_REASON,
            "git commit -m x": COMMIT_REASON,
            "git cherry-pick a1": COMMIT_REASON,
            "git revert a1": COMMIT_REASON,
            "git am < fix.patch": COMMIT_REASON,
            "git stash list": STASH_REASON,
            "git merge --ff-only x": MERGE_REASON,
            "git pull --ff-only": MERGE_REASON,
        }
        for command, reason in cases.items():
            self.assertEqual(risky_git.wf_decision(command), ("deny", reason), command)

    def test_publishing_a_package_is_denied(self) -> None:
        for command in DENY_PUBLISH:
            self.assertEqual(risky_git.wf_decision(command), ("deny", "publishes a package"), command)

    def test_wf_ship_asks_a_person(self) -> None:
        for command in ASK_SHIP:
            self.assertEqual(risky_git.wf_decision(command), ("ask", SHIP_REASON), command)

    def test_everyday_room_commands_pass_silently(self) -> None:
        noisy = {c: risky_git.wf_decision(c) for c in SILENT if risky_git.wf_decision(c) is not None}
        self.assertEqual(noisy, {}, "these would block or interrupt the everyday work of the room")

    def test_a_deny_beats_an_ask(self) -> None:
        self.assertEqual(
            risky_git.wf_decision("./wf ship && git push"), ("deny", "sends work to another repository (push)")
        )

    def test_non_string_or_empty_input_is_left_alone(self) -> None:
        for value in (None, 5, ["git", "commit"], "", "   "):
            self.assertIsNone(risky_git.wf_decision(value), value)  # type: ignore[arg-type]


class WfDecideTests(unittest.TestCase):
    def test_deny_text(self) -> None:
        self.assertEqual(
            risky_git.decide(bash("git commit -m x"), policy="wf"),
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "wf rule: this makes a commit outside wf: use ./wf commit. It was not run. Do not retry it or "
                        "look for a way around it. If you cannot go on without it, run ./wf ask 'the command you needed, "
                        "in plain words' --default 'what you do instead, without that command', post what it prints, "
                        "then stop."
                    ),
                }
            },
        )

    def test_an_ask_written_with_backticks_in_double_quotes_is_refused_too(self) -> None:
        # Found in review: backticks inside double quotes run the command inside, so the ask the deny text sends an
        # agent to write was refused as well when it was written in markdown. That is why the text says plain words.
        ask = './wf ask "I needed `rm -rf .playwright-mcp` to clean up" --default "a person removes it"'
        self.assertEqual(risky_git.wf_decision(ask)[0], "deny")

    def test_ask_text(self) -> None:
        self.assertEqual(
            risky_git.decide(bash("./wf ship"), policy="wf"),
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        "wf rule: this ships the task (./wf ship): it pushes the branch and opens the pull request, "
                        "or merges into main. A person must approve it."
                    ),
                }
            },
        )

    def test_the_deny_flag_changes_nothing_under_the_wf_policy(self) -> None:
        answer = risky_git.decide(bash("./wf ship"), deny=True, policy="wf")
        assert answer is not None
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_safe_commands_other_tools_and_malformed_payloads_are_left_alone(self) -> None:
        for payload in (
            bash("git status"),
            bash(5),
            {"tool_name": "Read", "tool_input": {"file_path": "x"}},
            {"tool_name": "Edit", "tool_input": {"command": "git commit -m x"}},
            {"tool_name": "Bash"},
            {"tool_name": "Bash", "tool_input": "git commit"},
            None,
            [],
            {},
        ):
            self.assertIsNone(risky_git.decide(payload, policy="wf"), payload)

    def test_any_other_policy_keeps_the_default_behaviour(self) -> None:
        for policy in (None, "", "WF", "other"):
            answer = risky_git.decide(bash("git reset --hard x"), policy=policy)
            assert answer is not None, policy
            self.assertEqual(
                answer["hookSpecificOutput"]["permissionDecisionReason"],
                "Preflight rule: this rewrites git history or moves a branch (reset). A person must approve it.",
            )
            self.assertIsNone(risky_git.decide(bash("git commit -m x"), policy=policy), policy)


class WfProcessTests(unittest.TestCase):
    """Run it the way Claude Code does: JSON on stdin, JSON (or nothing) on stdout, always exit 0."""

    def decision(self, command: str, *args: str) -> str:
        """The permissionDecision printed for COMMAND, or "" when the hook printed nothing."""
        done = run_hook(bash(command), args=args)
        self.assertEqual(done.returncode, 0, (command, args, done.stderr))
        return json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] if done.stdout else ""

    def test_policy_wf_denies_asks_and_stays_silent(self) -> None:
        for args in (("--policy", "wf"), ("--policy=wf",)):
            with self.subTest(args=args):
                self.assertEqual(self.decision("git commit -m x", *args), "deny")
                self.assertEqual(self.decision("./wf ship", *args), "ask")
                self.assertEqual(self.decision("git status", *args), "")

    def test_another_policy_value_keeps_the_default_behaviour(self) -> None:
        for args in (("--policy", "other"), ("--policy=nope",), ("--policy",)):
            with self.subTest(args=args):
                self.assertEqual(self.decision("git reset --hard x", *args), "ask")
                self.assertEqual(self.decision("git commit -m x", *args), "")

    def test_garbage_input_never_blocks_work(self) -> None:
        for raw in ("", "not json", "{", "[]", "null"):
            done = run_hook(None, raw=raw, args=("--policy", "wf"))
            self.assertEqual((done.returncode, done.stdout), (0, ""), raw)


if __name__ == "__main__":
    unittest.main()


class QuotedOptionTests(unittest.TestCase):
    """Found by the review: a double-quoted option value must not hide the git subcommand behind it."""

    def test_a_quoted_folder_or_value_still_shows_the_subcommand(self) -> None:
        for command in ('git -C "/Users/me/code/sketchroom" push origin t1-dup',
                        'git -C "/p q" commit -m x', 'git -c user.name="A B" commit -m x',
                        'git -C "/p" reset --hard x', 'git -C "/p" switch main', 'git -C "/p" stash'):
            with self.subTest(command=command):
                found = risky_git.wf_decision(command)
                self.assertIsNotNone(found, command)
                self.assertEqual(found[0], "deny")
        self.assertEqual(risky_git.risky_reason('git -C "/p" reset --hard x'), "rewrites git history or moves a branch (reset)")

    def test_text_in_double_quotes_is_still_only_text(self) -> None:
        for command in ('./wf commit --step 1 -m "git push later" -- a.txt', 'echo "git reset --hard is risky"',
                        'git commit -m "docs: rm -rf is dangerous"'):
            self.assertNotEqual(risky_git.wf_decision(command), ("ask", SHIP_REASON), command)
        self.assertIsNone(risky_git.wf_decision('./wf commit --step 1 -m "git push later" -- a.txt'))
