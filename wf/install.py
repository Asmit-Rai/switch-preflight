"""`wf init`: set up a project folder once (spec sections 7, 8 and 10). A second run changes nothing.

It writes `.workflow/project.toml`, the `./wf` launcher, the three git hooks, the exclude entries that
keep wf's files out of git, and merges `.claude/settings.json` (a person's entries are kept; the old
approval hook is replaced by the one with `--policy wf`; a `.bak` keeps the file as it was).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import config_path, load_config
from .errors import WfError
from .gitops import Git

KIT = Path(__file__).resolve().parent.parent
HOOK_MARK = "# wf git hook"
GIT_HOOKS = ("pre-commit", "pre-merge-commit", "pre-push")
# Seeded from examples/claude-settings.json, without git switch, git add and git commit.
BASE_ALLOW = (
    "Bash(./wf:*)",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)", "Bash(git branch:*)",
    "Bash(git rev-parse:*)", "Bash(git clone:*)",
    "Bash(npm ci:*)", "Bash(npm install:*)", "Bash(npm test:*)", "Bash(npm run:*)", "Bash(npm start:*)",
    "Bash(npx:*)", "Bash(node:*)",
    "Bash(curl:*)", "Bash(mkdir:*)", "Bash(sleep:*)", "Bash(lsof:*)", "Bash(kill:*)",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)", "Bash(find:*)",
)
DIRECTORIES = ("/tmp", "/private/tmp")
COVERED = {"npm", "npx", "node", "git", "curl", "mkdir", "sleep", "lsof", "kill", "ls", "cat", "grep", "find"}


def hook_command(kit: Path) -> str:
    return f'"{sys.executable}" "{kit}/hooks/risky_git.py" --policy wf'


def shim(kit: Path, name: str) -> str:
    return (f"#!/bin/sh\n{HOOK_MARK}: written by wf init. The rules live in the kit.\n"
            f'exec "{sys.executable}" "{kit}/wf/githooks.py" {name} "$@"\n')


def launcher(kit: Path) -> str:
    return (
        f"#!{sys.executable}\n"
        '"""./wf: the wf launcher of this project folder (written by `wf init`; git ignores it)."""\n'
        "import sys\n"
        "from pathlib import Path\n\n"
        "if sys.version_info < (3, 11):\n"
        '    raise SystemExit("wf needs Python 3.11 or newer")\n'
        f"sys.path.insert(0, {str(kit)!r})\n"
        "from wf.cli import main  # noqa: E402\n\n"
        "raise SystemExit(main(sys.argv[1:], project=Path(__file__).resolve().parent))\n"
    )


def _detect(folder: Path) -> tuple[str, list[str]]:
    """The install command and the suite, read from the project (package.json, else Python tests)."""
    package = folder / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, ValueError, AttributeError):
            scripts = {}
        install = "npm ci" if (folder / "package-lock.json").is_file() else "npm install"
        suite = ["npm test"] if "test" in scripts else []
        suite += [f"npm run {name}" for name in ("lint", "typecheck") if name in scripts]
        return install, suite
    if (folder / "tests").is_dir() and any(folder.glob("tests/test_*.py")):
        return "", ["python3 -m unittest discover -s tests"]
    return "", []


def _toml_list(values: list) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def render_toml(kit: Path, main: str, install: str, suite: list[str], ports: list[int], rules: str) -> str:
    lines = [
        "# wf settings for this project (written by wf init; edit freely; wf reads it on every command)",
        f"kit = {json.dumps(str(kit))}",
        f"main = {json.dumps(main)}",
        f"install = {json.dumps(install)}          # run in the tester's clean clone",
        f"suite = {_toml_list(suite)}",
        "max_loops = 3          # review CHANGES and test FAIL together; at the limit a person decides",
        "run_timeout = 600      # seconds before a step, the suite or an install is stopped (keep it under the agents' command limit)",
        f"ports = {_toml_list(ports)}",
    ]
    lines.append(f"rules = {json.dumps(rules)}" if rules else '# rules = "rules/oss-release.toml"   # a Preflight ruleset (relative to the kit): the ship gate runs it')
    lines += ["", "# [aliases]   # only if the room uses other aliases", '# builder = "wf-builder"', ""]
    return "\n".join(lines)


def _write_if_changed(path: Path, text: str, mode: int | None = None) -> bool:
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    changed = old != text
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if mode is not None and (os.stat(path).st_mode & 0o777) != mode:
        path.chmod(mode)
        changed = True
    return changed


def command_limit_ms(run_timeout: float) -> str:
    """The agents' Bash command limit: a minute more than wf's own, so wf always stops a run first."""
    return str(int((run_timeout + 60) * 1000))


def merge_settings(folder: Path, kit: Path, extra: list[str], run_timeout: float = 600.0) -> bool:
    path = folder / ".claude" / "settings.json"
    original = path.read_text(encoding="utf-8") if path.is_file() else None
    try:
        data = json.loads(original) if original else {}
    except ValueError as exc:
        raise WfError(f".claude/settings.json is not valid JSON ({exc}). Fix it by hand, then run init again.") from None
    if not isinstance(data, dict):
        raise WfError(".claude/settings.json is not a JSON object. Fix it by hand, then run init again.")
    permissions = data.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    for rule in [*BASE_ALLOW, *extra]:
        if rule not in allow:
            allow.append(rule)
    directories = permissions.setdefault("additionalDirectories", [])
    for directory in DIRECTORIES:
        if directory not in directories:
            directories.append(directory)
    env = data.setdefault("env", {})
    if isinstance(env, dict):  # Claude Code stops a Bash command after 2 minutes unless these say more
        for key in ("BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS"):
            env.setdefault(key, command_limit_ms(run_timeout))
        # Every Bash command starts in the project folder again, so a `cd` into a clone never strands ./wf.
        env.setdefault("CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR", "1")
    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    for entry in pre:
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            entry["hooks"] = [h for h in entry["hooks"] if not (isinstance(h, dict) and "risky_git.py" in str(h.get("command", "")))]
    pre[:] = [e for e in pre if not (isinstance(e, dict) and isinstance(e.get("hooks"), list) and not e["hooks"])]
    bash = next((e for e in pre if isinstance(e, dict) and e.get("matcher") == "Bash" and isinstance(e.get("hooks"), list)), None)
    if bash is None:
        bash = {"matcher": "Bash", "hooks": []}
        pre.append(bash)
    bash["hooks"].insert(0, {"type": "command", "command": hook_command(kit)})
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if text == original:
        return False
    if original is not None:
        (folder / ".claude" / "settings.json.bak").write_text(original, encoding="utf-8")
    _write_if_changed(path, text)
    return True


def init(folder: Path, kit: Path = KIT, *, main: str | None = None, install: str | None = None,
         suite: list[str] | None = None, ports: str | None = None, rules: str | None = None) -> str:
    git = Git(folder)
    if not folder.is_dir() or not git.is_repo():
        raise WfError(f"{folder} is not a git repository. Run init on the project's own folder.")
    top = Path(git.out("rev-parse", "--show-toplevel")).resolve()
    if top != folder.resolve():
        raise WfError(f"Run init on the top folder of the repository: {top}")
    git_dir = Path(git.out("rev-parse", "--absolute-git-dir"))
    notes: list[str] = []
    toml = config_path(folder)
    if toml.is_file():
        notes.append("kept .workflow/project.toml (edit it by hand to change it)")
        config = load_config(folder)
    else:
        found_install, found_suite = _detect(folder)
        try:
            port_list = [int(p) for p in (ports or "").replace(" ", "").split(",") if p]
        except ValueError:
            raise WfError("--ports takes port numbers, like 4321,4399") from None
        _write_if_changed(toml, render_toml(kit, main or "main", found_install if install is None else install,
                                            found_suite if suite is None else suite, port_list, rules or ""))
        notes.append("wrote .workflow/project.toml")
        config = load_config(folder)
    if not git.branch_exists(config.main):
        notes.append(f"there is no branch {config.main} yet: set main in .workflow/project.toml")
    if _write_if_changed(folder / "wf", launcher(kit), 0o755):
        notes.append("wrote the ./wf launcher")
    hooks_path = git.run("config", "--get", "core.hooksPath", check=False).stdout.strip()
    if hooks_path:
        notes.append(f"core.hooksPath is set ({hooks_path}): wf's git hooks were not installed; ./wf doctor reports it")
    else:
        for name in GIT_HOOKS:
            path = git_dir / "hooks" / name
            if path.is_file() and HOOK_MARK not in path.read_text(encoding="utf-8", errors="replace"):
                notes.append(f"a {name} hook that is not wf's is there: left it; ./wf doctor reports it")
                continue
            if _write_if_changed(path, shim(kit, name), 0o755):
                notes.append(f"installed the {name} git hook")
    wanted = ["/wf", "/.workflow/", "/.claude/settings.json.bak", ".DS_Store", ".playwright-mcp/"]
    if not git.tracked(".claude/settings.json"):
        wanted.insert(2, "/.claude/settings.json")
    exclude = git_dir / "info" / "exclude"
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    missing = [line for line in wanted if line not in text.splitlines()]
    if missing:
        block = ("" if not text or text.endswith("\n") else "\n") + "# wf: its files, and the browser tool's output, never go into git\n" + "\n".join(missing) + "\n"
        _write_if_changed(exclude, text + block)
        notes.append("added wf's files to .git/info/exclude")
    extra = []
    for command in (config.install, *config.suite, config.e2e, config.start):
        word = command.split()[0] if command.split() else ""
        if word and word not in COVERED and f"Bash({word}:*)" not in extra:
            extra.append(f"Bash({word}:*)")
    if merge_settings(folder, kit, extra, config.run_timeout):
        notes.append("merged .claude/settings.json (the old file is in .claude/settings.json.bak)")
    if git.tracked(".claude/settings.json") and git.run("diff", "--quiet", "--", ".claude/settings.json", check=False).returncode:
        notes.append(".claude/settings.json is tracked and changed: a person commits it in a terminal with "
                     'WF_SHIP=1 git commit -m "wf settings" -- .claude/settings.json (main refuses other commits)')
    lines = [f"wf is set up in {folder}."] + [f"- {note}" for note in notes] if notes else [f"wf is set up in {folder}: nothing to change."]
    return "\n".join([*lines, f"Next: cd {folder} && ./wf doctor"])


def cmd_init(args, cwd: Path) -> str:
    folder = Path(args.folder).expanduser()
    folder = (folder if folder.is_absolute() else cwd / folder).resolve()
    return init(folder, KIT, main=args.main, install=args.install, suite=args.suite, ports=args.ports, rules=args.rules)
