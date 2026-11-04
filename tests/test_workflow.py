"""workflow/: the six-agent test-first team (a Switch template), the Sketchroom folder script, the bug log.

The template is read line by line, with no YAML library (this checker is standard library only). The
checks hold the Switch lessons we paid for in earlier rehearsals, so an edit made while fixing a
workflow bug cannot quietly bring an old problem back:
  * a new agent answers only its owner unless it says `addressing: anyone`
  * Switch stores agent instructions exactly as written, so `{placeholders}` there stay literal
  * "changed no file" is code, not a promise: the read-only roles have no tool that can write
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from helpers import ROOT, commit_all, git, git_env, write

WORKFLOW = ROOT / "workflow"
TEMPLATE = WORKFLOW / "team.template.yaml"
ROLES = ("analyst", "planner", "executor", "reviewer", "tester", "advisor")
WRITERS = {"Write", "Edit", "NotebookEdit"}
BUGS_HEADER = "| Run | Agent | What happened (quote it) | What should have happened | Fix | Rerun result |"


def section(text: str, key: str) -> list[str]:
    """The lines of the top-level block `key:`, up to the next top-level key."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(key + ":"))
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"[A-Za-z_]+:", lines[i])), len(lines))
    return lines[start + 1 : end]


def listed(value: str) -> set[str]:
    """`[Write, Edit]` -> {"Write", "Edit"}."""
    return set(re.findall(r"[A-Za-z]+", value))


def parse_agents(text: str) -> dict[str, dict[str, Any]]:
    """{name: {"fields": {...}, "config": {...}}} for each `- name:` entry under `agents:`."""
    entries: list[list[str]] = []
    for line in section(text, "agents"):
        if line.startswith("  - name:"):
            entries.append([])
        if entries:
            entries[-1].append(line)
    agents: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = re.match(r'  - name: "?([^"\n]+)"?', entry[0]).group(1)  # type: ignore[union-attr]
        fields: dict[str, str] = {}
        config: dict[str, str] = {}
        i = 1
        while i < len(entry):
            m = re.match(r"    ([A-Za-z_]+):\s*(.*)$", entry[i])
            i += 1
            if not m:
                continue
            key, value = m.groups()
            if key == "instructions":
                body = []
                while i < len(entry) and (entry[i].startswith("      ") or not entry[i].strip()):
                    body.append(entry[i][6:])
                    i += 1
                fields[key] = "\n".join(body).strip()
            elif key == "advanced_config":
                while i < len(entry) and entry[i].startswith("      "):
                    k, v = entry[i].strip().split(":", 1)
                    config[k] = v.strip()
                    i += 1
            else:
                fields[key] = value.strip().strip('"')
        agents[name] = {"fields": fields, "config": config}
    return agents


class TemplateCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = TEMPLATE.read_text(encoding="utf-8")
        cls.agents = parse_agents(cls.text)

    def agent(self, role: str) -> dict[str, Any]:
        return self.agents["{team}-" + role]

    def test_a_real_yaml_parser_reads_it_the_same_way(self) -> None:
        # The line-based checks below cannot see a YAML syntax error, such as a colon followed by a space in a
        # plain value. Ruby ships a YAML parser, so use it when it is there.
        ruby = shutil.which("ruby")
        if not ruby:
            self.skipTest("no ruby on this machine: only the line-based checks ran")
        script = "require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0]))"
        proc = subprocess.run([ruby, "-e", script, str(TEMPLATE)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        data = json.loads(proc.stdout)
        self.assertEqual(sorted(a["name"] for a in data["agents"]), sorted(self.agents))
        for a in data["agents"]:
            with self.subTest(agent=a["name"]):
                mine = self.agents[a["name"]]
                self.assertEqual(a["addressing"], "anyone")
                self.assertEqual(" ".join(a["instructions"].split()), " ".join(mine["fields"]["instructions"].split()))
        self.assertEqual(data["room"]["agents"], ["{team}-" + role for role in ROLES])
        self.assertTrue(data["kickoff"].startswith("@{team}-analyst"))

    def test_there_are_six_agents_named_after_the_team_prefix(self) -> None:
        self.assertEqual(sorted(self.agents), sorted("{team}-" + role for role in ROLES))

    def test_every_agent_is_open_to_the_room_and_shares_one_folder(self) -> None:
        for name, agent in self.agents.items():
            with self.subTest(agent=name):
                fields = agent["fields"]
                self.assertEqual(fields.get("addressing"), "anyone")  # the default refuses everyone but the owner
                self.assertEqual(fields.get("directory"), "{directory}")  # the spec: one folder, the repo root
                for key in ("display_name", "description", "provider", "location", "instructions"):
                    self.assertTrue(fields.get(key), f"{name} has no {key}")

    def test_agent_instructions_hold_no_placeholders(self) -> None:
        for name, agent in self.agents.items():
            with self.subTest(agent=name):
                self.assertNotRegex(agent["fields"]["instructions"], r"[{}]", "Switch stores instructions raw")

    def test_instructions_stay_inside_the_switch_limits(self) -> None:
        for name, agent in self.agents.items():
            with self.subTest(agent=name):
                self.assertLess(len(agent["fields"]["instructions"].encode()), 30_000)  # limit 32 KiB
        self.assertLess(len(self.text.encode()), 1_000_000)  # limit 1 MiB

    def test_analyst_and_planner_have_no_tool_that_can_change_a_file_or_run_a_command(self) -> None:
        for role in ("analyst", "planner"):
            with self.subTest(role=role):
                tools = listed(self.agent(role)["config"].get("tools", ""))
                self.assertTrue(tools, "give it a `tools` allowlist")
                self.assertFalse(tools & (WRITERS | {"Bash"}), tools)

    def test_reviewer_and_advisor_cannot_write_files(self) -> None:
        for role in ("reviewer", "advisor"):
            with self.subTest(role=role):
                self.assertLessEqual(WRITERS, listed(self.agent(role)["config"].get("disallowedTools", "")))

    def test_the_room_lists_the_six_agents_with_their_aliases(self) -> None:
        room = "\n".join(section(self.text, "room"))
        for role in ROLES:
            with self.subTest(role=role):
                self.assertIn('"{team}-' + role + '"', room)
                self.assertRegex(room, re.escape('"{team}-' + role + '": sr-' + role))

    def test_no_alias_is_a_plain_role_word(self) -> None:
        # Run 1: an alias such as `analyst` is also the name of any agent made earlier with that name (the six agents
        # of 3 Nov), so Switch warned "Analyst (analyst) isn't in this room" on every mention (BUGS.md, row 1).
        room = "\n".join(section(self.text, "room"))
        aliases = re.findall(r'^\s+"\{team\}-\w+": (\S+)\s*$', room, re.M)
        self.assertEqual(len(aliases), len(ROLES))
        self.assertFalse(set(aliases) & set(ROLES), aliases)
        self.assertEqual(sorted(aliases), sorted("sr-" + role for role in ROLES))

    def test_every_mention_in_the_instructions_is_a_prefixed_alias(self) -> None:
        allowed = {"sr-" + role for role in ROLES}
        texts = {name: agent["fields"]["instructions"] for name, agent in self.agents.items()}
        texts["room"] = "\n".join(section(self.text, "room"))
        for name, text in texts.items():
            with self.subTest(where=name):
                # `@types/react` and `@vitejs/plugin-react` are npm packages, not mentions
                mentions = set(re.findall(r"@([A-Za-z][\w-]*)(?![\w/-])", text))
                self.assertLessEqual(mentions, allowed, "a plain @analyst would match an old agent of that name")

    def test_no_agent_is_told_to_mention_itself(self) -> None:
        # Run 1: "Start your reply with @analyst" in the analyst's own messages made Switch warn that the analyst was
        # not in the room, and the analyst had to add a note about it to its brief (BUGS.md, row 1).
        for role in ROLES:
            with self.subTest(role=role):
                text = " ".join(self.agent(role)["fields"]["instructions"].split())  # a wrapped line is the same words
                self.assertNotIn("@sr-" + role, text)
                self.assertNotIn("@" + role, text)
                self.assertNotIn("Start your reply with @", text)

    def test_the_room_description_tells_a_person_how_to_address_an_agent(self) -> None:
        room = "\n".join(section(self.text, "room"))
        description = re.search(r'^\s+description: "(.*)"\s*$', room, re.M)
        self.assertTrue(description, "the room has a description (it is the Discord channel topic)")
        for role in ROLES:
            with self.subTest(role=role):
                self.assertIn("@sr-" + role, description.group(1))

    def test_no_room_role_or_document_is_named_like_an_alias(self) -> None:
        room = "\n".join(section(self.text, "room"))
        names = set(re.findall(r"^\s+- name: (\S+)\s*$", room, re.M))
        self.assertFalse(names & set(ROLES), "a role named like an alias clashed with the alias in the first template")
        self.assertFalse(names & {"sr-" + role for role in ROLES})

    def test_every_placeholder_is_a_declared_parameter(self) -> None:
        params = set(re.findall(r"^  (\w+):\s*$", "\n".join(section(self.text, "params")), re.M))
        where = section(self.text, "room") + section(self.text, "kickoff")
        for agent_name, agent in self.agents.items():
            where.append(agent_name)
            where += [agent["fields"][key] for key in ("provider", "location", "directory")]
        used = set(re.findall(r"\{(\$?\w+)\}", "\n".join(where)))
        self.assertLessEqual(used - {"$creator"}, params)
        self.assertLessEqual({"part", "run", "last_task", "team"}, params)

    def test_the_kickoff_wakes_only_the_analyst_and_names_the_run(self) -> None:
        kickoff = "\n".join(section(self.text, "kickoff"))
        self.assertEqual(re.findall(r"@(\{team\}-\w+)", kickoff), ["{team}-analyst"])
        for placeholder in ("{part}", "{run}", "{last_task}"):
            self.assertIn(placeholder, kickoff)

    def test_each_agent_still_holds_the_rules_that_define_its_job(self) -> None:
        must_say = {
            # Run 1, bug 2: the brief held only the section 6 items, so a section 4 rule (dependencies listed in the
            # README) was never planned, built or tested.
            "analyst": ["confirm brief", "list_participants", "brief-T<n>", "sections 1, 2, 4 and 6", "(section 4)",
                        "at most three more numbered items"],
            "planner": ["approve", "exact command that runs", "never hand on a step that is not approved",
                        "plan-T<n>", "APPROVED:"],
            "executor": ["fail for the right reason", "least code", "whole suite", "skips or deletes a test",
                         "git switch -c", "never switch to main", "plan-T<n>", "package-lock.json",
                         "stop every server", "step <k> test:", "git add -A", "@sr-planner please fix step",
                         "kill <pid>",
                         # Run 1, bug 3: a RED line was retyped wrongly once.
                         "from memory", "compare it with the command output"],
            # Run 1, bug 5: the Reviewer's hand-off to the Tester carried no evidence, against the room rule.
            "reviewer": ["tests first", "mock away", "approve", "changes needed", "plan-T<n>", "step <k> test:",
                         "@sr-executor approved:", "make no handoff", "@sr-tester please test T<n> on <branch> at <commit>"],
            # Run 2, bug 6: the tester ran `rm -rf dist`, the approval hook asked, and it waited for a click that
            # nobody knew was needed.
            "tester": ["git clone --branch", "npm ci", "PASS", "FAIL", "never take", "EVERY", "port 4321",
                       "headless browser", "step <k> test:", "make no handoff", "write no files",
                       "never delete anything", "git switch --detach", "its own second clone"],
            # Run 1, bug 4: the Advisor called a package the Reviewer had counted inside section 4 "outside the plan".
            "advisor": ["closing note", "no rule slipped", "no issues", "plan-T<n>", "step <k> test:",
                        "named in its commit"],
        }
        def words(text: str) -> str:  # line breaks must not matter: a rewrapped paragraph is still the same rule
            return " ".join(text.split()).lower()

        for role, phrases in must_say.items():
            text = words(self.agent(role)["fields"]["instructions"])
            for phrase in phrases:
                with self.subTest(role=role, phrase=phrase):
                    self.assertIn(phrase.lower(), text)
        room = words("\n".join(section(self.text, "room")))
        for phrase in ("SPEC.md", "WORKFLOW BUG:", "list_participants", "start-", "One part per run",
                       "not the agent name",  # run 1, bug 5: hand-offs went to @tdd-executor, not the alias
                       "approval card", "a command that deletes files"):  # run 2, bug 6
            with self.subTest(room=phrase):
                self.assertIn(phrase.lower(), room)


class FilesCase(unittest.TestCase):
    def test_the_spec_is_the_one_the_user_gave_us(self) -> None:
        spec = (WORKFLOW / "SPEC.md").read_text(encoding="utf-8")
        self.assertTrue(spec.startswith("# Sketchroom: build spec (v2)"))
        for part in ("Part A: scaffold", "Part B: whiteboard core", "Part C: agents can draw shapes", "Part D: diagrams and docs"):
            self.assertIn(part, spec)
        self.assertNotIn("EXTERNAL_UNTRUSTED_CONTENT", spec)  # the wrapper of the chat attachment is not part of it

    def test_the_bug_log_has_the_header_row_from_the_spec(self) -> None:
        rows = [line for line in (WORKFLOW / "BUGS.md").read_text(encoding="utf-8").splitlines() if line.startswith("|")]
        self.assertEqual(rows[0], BUGS_HEADER)
        self.assertRegex(rows[1], r"^\|(?:-+\|){6}$")

    def test_the_permission_file_allows_the_test_tools_and_names_the_approval_hook(self) -> None:
        settings = json.loads((WORKFLOW / "claude-settings.json").read_text(encoding="utf-8"))
        allow = settings["permissions"]["allow"]
        for rule in ("Bash(npm ci:*)", "Bash(npm test:*)", "Bash(npm run:*)", "Bash(npx:*)", "Bash(git commit:*)",
                     "Bash(kill:*)", "Bash(lsof:*)"):
            self.assertIn(rule, allow)
        # never allowed without a person: sending work away, rewriting history, deleting files, killing by name
        for word in ("push", "reset", "rebase", "clean", "pkill", "killall", "rm "):
            self.assertFalse([rule for rule in allow if word in rule], word)
        self.assertFalse([rule for rule in allow if rule.startswith("Bash(rm")])
        self.assertIn("__HOOK__", settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])


class SetupCase(unittest.TestCase):
    def setup_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(WORKFLOW / "setup.sh"), *args], capture_output=True, text=True, env=git_env(), timeout=60
        )

    def make_folder(self, tmp: str) -> Path:
        folder = Path(tmp) / "sketchroom"
        proc = self.setup_script(str(folder))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return folder

    def test_setup_makes_the_folder_the_agents_share(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_folder(tmp)
            self.assertEqual((folder / "SPEC.md").read_text(), (WORKFLOW / "SPEC.md").read_text())
            self.assertEqual(git(folder, "rev-parse", "start-a"), git(folder, "rev-parse", "HEAD"))
            self.assertEqual(git(folder, "branch", "--show-current"), "main")
            self.assertEqual(git(folder, "ls-files"), "SPEC.md")
            self.assertEqual(git(folder, "status", "--porcelain"), "")  # the permission file is excluded, not tracked
            settings = json.loads((folder / ".claude" / "settings.json").read_text())
            self.assertIn("Bash(npm test:*)", settings["permissions"]["allow"])
            hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertNotIn("__HOOK__", hook)
            self.assertIn(str(ROOT / "hooks" / "risky_git.py"), hook)
            self.assertTrue((ROOT / "hooks" / "risky_git.py").is_file())

    def test_setup_twice_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_folder(tmp)
            before = git(folder, "rev-parse", "HEAD")
            proc = self.setup_script(str(folder))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("already set up", proc.stdout)
            self.assertEqual(git(folder, "rev-parse", "HEAD"), before)

    def test_reset_puts_the_folder_back_at_the_start_and_keeps_the_failed_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_folder(tmp)
            git(folder, "switch", "-q", "-c", "part-a-run1")
            write(folder, "package.json", "{}")
            commit_all(folder, "step 1")
            write(folder, "package.json", "changed but not committed")
            write(folder, "node_modules/left-over.js", "junk")
            write(folder, ".claude/settings.json", "{}")  # an old permission file: reset must install the current one
            proc = self.setup_script("--reset", str(folder))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(git(folder, "rev-parse", "HEAD"), git(folder, "rev-parse", "start-a"))
            self.assertFalse((folder / "package.json").exists())
            self.assertFalse((folder / "node_modules").exists())
            settings = json.loads((folder / ".claude" / "settings.json").read_text())
            self.assertIn("Bash(npm test:*)", settings["permissions"]["allow"])
            self.assertEqual(git(folder, "status", "--porcelain"), "")  # still hidden from git
            self.assertIn("part-a-run1", git(folder, "branch", "--list"))  # evidence of the failed run is kept

    def test_reset_refuses_an_unknown_tag_and_a_folder_that_was_never_set_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_folder(tmp)
            proc = self.setup_script("--reset", str(folder), "start-z")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("no such tag", proc.stderr)
            empty = Path(tmp) / "nothing-here"
            empty.mkdir()
            proc = self.setup_script("--reset", str(empty))
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("not set up", proc.stderr)


if __name__ == "__main__":
    unittest.main()
