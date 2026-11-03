#!/usr/bin/env python3
"""Create a small hackathon-style git repo to demo Preflight on.

    python3 examples/make_sample.py DEST            # the broken version: 6 planted defects
    python3 examples/make_sample.py DEST --fixed    # the all-green version

DEST must not exist or must be empty. The result is a fresh repo with fixed author, committer and
dates, so two runs produce identical commits (only the folder path differs). Nothing is pushed:
`origin` points at a made-up GitHub URL.

Every defect has a natural fix that an agent can make on a branch: write the missing sections, add
the LICENSE, replace the dead link, fix the code (not the test) so the test passes, add the video
link. The token is the one risky step, so it needs the Captain: it sits only in the last commit, so
it is purged with `git reset --soft HEAD~1`, deleting the line and committing again.

Check it with the sample ruleset, which is the contest ruleset with `repo-public` made advisory
(the made-up origin can never be public):

    python3 -m preflight run --project DEST --rules examples/sample.rules.toml --format chat

Standard library only. The fake token in the broken version is built at runtime, so no token-shaped
text is ever stored in this file.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ORIGIN = "https://github.com/example-team/sample-submission.git"
AUTHOR = "Sample Team"
EMAIL = "sample@example.com"
# The contest starts 2026-11-04T00:01:00+05:30; all three commits are after it, so no history rewrite is
# needed for the dates.
AFTER_START = ("2026-11-04T00:30:00+05:30", "2026-11-04T00:40:00+05:30", "2026-11-04T00:50:00+05:30")
VIDEO = "https://www.youtube.com/watch?v=mA4_v5fQDYE"

# The 6 planted defects: (number, what is wrong, ids of the flip-the-switch checks it turns red).
# Each one turns exactly one check red.
DEFECTS = (
    (1, "README lacks the 'Agents we connected' and 'Reused vs built' sections", ("readme-headings",)),
    (2, "there is no LICENSE file", ("license",)),
    (3, "the README links a dead placeholder URL (docs.example.invalid)", ("links-ok",)),
    (4, "settings_local.py commits a (fake) GitHub token, in the last commit", ("secret-scan",)),
    (5, "roll_call() joins the names with a space, not ', ', so a test fails", ("tests-pass",)),
    (6, "the README has no demo-video link", ("video-link-present",)),
)


def fake_github_token() -> str:
    """A GitHub-token-shaped string, assembled at runtime from harmless pieces."""
    body = "A1b2C3d4E5f6G7h8I9" + "j0K1l2M3n4O5p6Q7r8"
    assert len(body) == 36
    return "gh" + "p" + "_" + body


APP = '''"""Roll Call: print who is in the room as one comma-separated line."""

import sys


def roll_call(names: list[str]) -> str:
    """Return the names on one line, separated by a comma and a space: Ada, Grace, Linus."""
    return {joiner}.join(names)


if __name__ == "__main__":
    print(roll_call(sys.argv[1:]))
'''

TEST_ROLL_CALL = '''import unittest

from app import roll_call


class RollCallTest(unittest.TestCase):
    def test_names_are_separated_by_a_comma_and_a_space(self):
        self.assertEqual(roll_call(["Ada", "Grace", "Linus"]), "Ada, Grace, Linus")

    def test_one_name(self):
        self.assertEqual(roll_call(["Ada"]), "Ada")

    def test_empty_list(self):
        self.assertEqual(roll_call([]), "")


if __name__ == "__main__":
    unittest.main()
'''

ROOM_YAML = """# Switch group template (a stub for the sample project)
name: roll-call-room
roles:
  - name: captain
    exclusive: true
agents:
  - name: scout
  - name: inspector
"""

GITIGNORE = """.preflight/
__pycache__/
*.pyc
"""

LICENSE = """MIT License

Copyright (c) 2026 Sample Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def app_py(fixed: bool) -> str:
    """The app. The broken one joins the names with a space instead of ", " (defect 5)."""
    return APP.format(joiner='", "' if fixed else '" "')


def settings_py(fixed: bool) -> str:
    """settings_local.py. The broken one holds a (fake) token (defect 4); the fixed one reads it from the environment."""
    if fixed:
        return (
            '"""Local settings for Roll Call. The token is read from the environment, never stored here."""\n'
            "\n"
            "from os import environ\n"
            "\n"
            'ROOM = "roll-call"\n'
            'CHANNEL = "#roll-call"\n'
            'GITHUB_TOKEN = environ.get("GITHUB_TOKEN", "")\n'
        )
    return (
        '"""Local settings for Roll Call on this laptop."""\n'
        "\n"
        'ROOM = "roll-call"\n'
        'CHANNEL = "#roll-call"\n'
        f'GITHUB_TOKEN = "{fake_github_token()}"\n'
    )


def readme(fixed: bool) -> str:
    """The sample README. The broken one omits two sections (defect 1), links a dead URL (3) and has no video (6)."""
    lines = [
        "# Roll Call",
        "",
        "A tiny sample submission for the Preflight demo. It is generated by `examples/make_sample.py`.",
        "",
    ]
    if fixed:
        lines += [f"Demo video: [Roll Call in action]({VIDEO})", ""]
    lines += [
        "## The problem",
        "",
        "Teams lose track of who is in the room, so a name gets missed and a message goes unanswered.",
        "",
        "## What it does",
        "",
        "`app.py` takes names on the command line and prints them as one comma-separated line.",
        "",
        "## Why Switch is central",
        "",
        "The roll call runs inside a Switch room, where people and agents share one list of who is present.",
        "",
    ]
    if fixed:
        lines += [
            "## Agents we connected",
            "",
            "`scout` reads the rules and drafts the checks. `inspector` runs them and posts the table.",
            "",
        ]
    lines += [
        "## How the channel is set up",
        "",
        "Create a Discord channel, invite the Switch app, and point the room at that channel."
        + ("" if fixed else " See the [setup guide](https://docs.example.invalid/setup)."),
        "",
        "## Run it in 10 minutes",
        "",
        "Clone the repo, then print a roll call from the command line:",
        "",
        "```",
        "python3 app.py Ada Grace Linus",
        "```",
        "",
        "It prints `Ada, Grace, Linus`.",
        "",
    ]
    if fixed:
        lines += [
            "## Reused vs built during the hackathon",
            "",
            "Everything here was written after the start. Only Switch itself and Python's standard library are reused.",
            "",
        ]
    return "\n".join(lines)


def _git_env() -> dict[str, str]:
    """Environment that makes git independent of the machine's config and gives fixed identity."""
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME=AUTHOR,
        GIT_AUTHOR_EMAIL=EMAIL,
        GIT_COMMITTER_NAME=AUTHOR,
        GIT_COMMITTER_EMAIL=EMAIL,
    )
    return env


def _git(dest: Path, *args: str, when: str | None = None) -> None:
    env = _git_env()
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    cmd = ["git", "-C", str(dest), "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *args]
    subprocess.run(cmd, check=True, env=env, stdout=subprocess.DEVNULL)


def _write(dest: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def _commit(dest: Path, message: str, when: str) -> None:
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", message, when=when)


def create_sample(dest: Path, fixed: bool = False) -> None:
    """Create the sample repo at `dest` (which must not exist or must be empty)."""
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise FileExistsError(f"{dest} already exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q")
    _git(dest, "symbolic-ref", "HEAD", "refs/heads/main")
    first, second, third = AFTER_START

    files = {"app.py": app_py(fixed), ".gitignore": GITIGNORE}
    if fixed:  # defect 2 is the missing LICENSE
        files["LICENSE"] = LICENSE
    _write(dest, files)
    _commit(dest, "Start the roll call script", first)

    _write(dest, {"README.md": readme(fixed), "tests/test_roll_call.py": TEST_ROLL_CALL, "templates/room.yaml": ROOM_YAML})
    _commit(dest, "Add README, tests and the room template", second)

    # Defect 4: the token is only in this last commit, so `git reset --soft HEAD~1` purges it.
    _write(dest, {"settings_local.py": settings_py(fixed)})
    _commit(dest, "Add local settings", third)

    _git(dest, "remote", "add", "origin", ORIGIN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a sample hackathon repo for Preflight.")
    parser.add_argument("dest", help="folder to create (must not exist or must be empty)")
    parser.add_argument("--fixed", action="store_true", help="the all-green version (no planted defects)")
    args = parser.parse_args(argv)
    dest = Path(args.dest).resolve()
    try:
        create_sample(dest, fixed=args.fixed)
    except FileExistsError as exc:
        print(f"make_sample: error: {exc}", file=sys.stderr)
        return 2
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"make_sample: error: {exc}", file=sys.stderr)
        return 1
    if args.fixed:
        print(f"Created the fixed (all-green) sample at {dest}")
    else:
        print(f"Created the sample at {dest} with {len(DEFECTS)} planted defects:")
        for number, text, _ in DEFECTS:
            print(f"  {number}. {text}")
    print(f"Try: python3 -m preflight run --project {dest} --rules examples/sample.rules.toml --format chat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
