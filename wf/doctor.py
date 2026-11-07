"""`wf doctor`: is this folder ready for a build room? Each problem comes with a one-line fix.

The manager runs it on `hello` and posts the result: "Ready. Tell me what you want built." or the list.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .config import config_path, load_config
from .context import Ctx
from .errors import WfError
from .install import GIT_HOOKS, HOOK_MARK

READY = "Ready. Tell me what you want built."


def _git_version() -> tuple[int, ...]:
    try:
        text = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ()
    match = re.search(r"(\d+)\.(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else ()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def cmd_doctor(ctx: Ctx, args) -> str:
    problems: list[str] = []
    notes: list[str] = []
    folder = ctx.project
    setup = f"python3 -m wf init {folder} (from the kit)"
    version = _git_version()
    if version < (2, 24):
        problems.append("git 2.24 or newer is needed (git switch and the pre-merge-commit hook): update git")
    if sys.version_info < (3, 11):
        problems.append("Python 3.11 or newer is needed: install it, then run init again")
    if not config_path(folder).is_file():
        problems.append(f"this folder is not set up for wf: run {setup}")
        raise WfError("\n".join(["Not ready:", *[f"- {p}" for p in problems], "Fix these, then run ./wf doctor again."]))
    try:
        config = load_config(folder)
    except WfError as exc:
        problems.append(f"{exc}: fix .workflow/project.toml")
        config = ctx.config
    git = ctx.git
    if not git.branch_exists(config.main):
        problems.append(f"there is no branch {config.main}: set main in .workflow/project.toml")
    if git.run("var", "GIT_COMMITTER_IDENT", check=False).returncode != 0:
        problems.append('git does not know who commits: git config user.name "<name>" and git config user.email "<email>"')
    state = ctx.load()
    entries = ctx.clean_entries(ctx.active(state))
    if entries:
        problems.append(f"the tree is not clean ({', '.join(entries[:3])}): commit or move them by hand before the first task")
    launcher = folder / "wf"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        problems.append(f"the ./wf launcher is missing: run {setup} again")
    if config.kit is None or not (config.kit / "wf" / "cli.py").is_file():
        problems.append(f"the kit in .workflow/project.toml ({config.kit}) has no wf: run {setup} again")
    settings = folder / ".claude" / "settings.json"
    commands: list[str] = []
    allow: list[str] = []
    try:
        data = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
        commands = [str(h.get("command", "")) for e in data.get("hooks", {}).get("PreToolUse", [])
                    for h in e.get("hooks", []) if isinstance(h, dict)]
        allow = list(data.get("permissions", {}).get("allow", []))
    except (OSError, ValueError, AttributeError, TypeError):
        problems.append(f".claude/settings.json cannot be read: fix it by hand, then run {setup} again")
    try:
        limit = int(str(data.get("env", {}).get("BASH_DEFAULT_TIMEOUT_MS", "0")))
    except (ValueError, AttributeError, NameError):
        limit = 0
    if limit < (config.run_timeout + 30) * 1000:
        problems.append(f"agents' commands stop after {max(limit, 120000) // 1000}s, before wf's run_timeout "
                        f"({config.run_timeout:.0f}s): run {setup} again, or set BASH_DEFAULT_TIMEOUT_MS in .claude/settings.json")
    if str(data.get("env", {}).get("CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR", "")) != "1":
        problems.append(f"agents' commands do not start in the project folder again after a cd: run {setup} again")
    if not any("risky_git.py" in c and "--policy wf" in c for c in commands):
        problems.append(f"the approval hook with --policy wf is not in .claude/settings.json: run {setup} again")
    hooks_path = git.run("config", "--get", "core.hooksPath", check=False).stdout.strip()
    git_dir = git.out("rev-parse", "--absolute-git-dir")
    if hooks_path:
        problems.append(f"core.hooksPath is set ({hooks_path}), so wf's git hooks do not run: unset it, then run {setup} again")
    else:
        for name in GIT_HOOKS:
            path = os.path.join(git_dir, "hooks", name)
            ok = (os.path.isfile(path) and os.access(path, os.X_OK)
                  and HOOK_MARK in Path(path).read_text(encoding="utf-8", errors="replace"))
            if not ok:
                problems.append(f"the {name} git hook is not wf's: run {setup} again")
    exclude = os.path.join(git_dir, "info", "exclude")
    excluded = Path(exclude).read_text(encoding="utf-8").splitlines() if os.path.isfile(exclude) else []
    if "/.workflow/" not in excluded or "/wf" not in excluded:
        problems.append(f"wf's files are not in .git/info/exclude: run {setup} again")
    if ".playwright-mcp/" not in excluded:
        notes.append(f".playwright-mcp/ is not in .git/info/exclude: run {setup} again, or a screenshot from the browser tool makes the tree unclean")
    if not config.suite:
        problems.append("the suite in .workflow/project.toml is empty: add the commands that test the project")
    for command in (config.install, *config.suite, config.e2e, config.start):
        word = command.split()[0] if command.split() else ""
        if word and not shutil.which(word) and not (folder / word).exists():
            problems.append(f"{word} is not installed (needed by: {command})")
    if config.rules:
        rules = config.rules_path()
        if rules is None or not rules.is_file():
            problems.append(f"the Preflight ruleset {rules} is not there: fix rules in .workflow/project.toml")
    for port in config.ports:
        if _port_in_use(port):
            problems.append(f"port {port} is in use: stop what holds it (lsof -ti :{port}), never an agent's job")
    if git.remote_url("origin"):
        if not shutil.which("gh"):
            notes.append("gh is not installed: ./wf ship will print a link instead of opening the pull request")
        elif subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL).returncode != 0:
            notes.append("gh is not logged in: ./wf ship will print a link instead of opening the pull request")
    else:
        notes.append(f"no remote: ./wf ship merges into {config.main} on this machine")
    for command in (config.install, *config.suite):
        word = command.split()[0] if command.split() else ""
        if word and not any(rule.startswith((f"Bash({word}:", f"Bash({word} ")) for rule in allow):
            notes.append(f"{word} is not in the allowlist: each run would ask for a card")
    if problems:
        shown = problems[:8] + ([f"... and {len(problems) - 8} more"] if len(problems) > 8 else [])
        raise WfError("\n".join(["Not ready:", *[f"- {p}" for p in shown], "Fix these, then run ./wf doctor again."]))
    return "\n".join([*[f"Note: {n}." for n in notes[:4]], READY])
