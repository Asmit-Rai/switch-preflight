"""`preflight doctor`: is this machine, and the agents' shared folder, ready for a run?

    kit/pf doctor [--folder DIR] [--switch-url URL] [--format table|chat]

DIR is the shared folder made by scripts/setup-demo.sh: it holds kit/, rules/, project/ and .claude/.
Ten checks run in order: python, git, claude, gh, layout, kit, settings, hook, rules, switch. Each one
says PASS, FAIL, WARN or SKIP in plain words and, when it is not a PASS, gives a one-line fix.

The doctor only looks. git runs with GIT_OPTIONAL_LOCKS=0, so reading never rewrites the index; the
approval hook is fed two sample tool calls on stdin; programs run without a shell and with a time limit.
Its only network call is the optional Switch health check (gh itself may ask GitHub whether the sign-in
still works). `gh auth status` names accounts and token scopes, so only its exit code is used.

Exit codes: 0 nothing failed (warnings are fine), 1 something failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import net
from .checks.base import clip
from .checks.secrets import redact
from .model import FAIL, PASS, SKIP, WARN
from .report import CHAT_LIMIT, TABLE_WIDTH
from .ruleset import parse_ruleset

TIMEOUT = 10.0  # seconds for each program the doctor starts
HEALTH_TIMEOUT = 3.0  # seconds for the Switch health check
MIN_PYTHON = (3, 11)
CHAT_LINE_MAX = 160  # ten lines of this, the title and the summary stay under CHAT_LIMIT

PF_RULE = "Bash(kit/pf:*)"
HOOK_SCRIPT = "kit/hooks/risky_git.py"
RISKY_SAMPLE = "git -C project reset --hard HEAD~1"  # the hook must ask a person
SAFE_SAMPLE = "git status"  # the hook must stay silent

SETTINGS_FIX = "a person runs scripts/setup-demo.sh; agents never edit this file"
HOOK_FIX = "a person restores it: git -C kit checkout -- hooks; agents never edit it"
KIT_FIX = "a person looks at git -C kit status; agents never change the checker"
GIT_FIX = "install git (on macOS: xcode-select --install)"
PYTHON_FIX = "install Python 3.11 or newer (python.org, or brew install python)"
CLAUDE_FIX = "install Claude Code, then run claude once to sign in"
RULES_FIX = "@scout drafts it and the Captain approves it"
SWITCH_FIX = "open Switch Console and start the local server"
SWITCH_HINT = "pass --switch-url http://127.0.0.1:<api port>"

# run(argv, stdin_text=None, timeout=TIMEOUT, cwd=None, env=None) -> CompletedProcess with text output.
# It raises OSError when the program cannot start and subprocess.TimeoutExpired when it is too slow.
Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_program(
    argv: Sequence[str],
    stdin_text: str | None = None,
    timeout: float = TIMEOUT,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Start a program without a shell, give it `stdin_text` on stdin (or nothing) and capture its output.

    A program still running after `timeout` seconds is killed and subprocess.TimeoutExpired is raised.
    """
    return subprocess.run(
        list(argv),
        input=stdin_text,
        stdin=subprocess.DEVNULL if stdin_text is None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        check=False,
    )


@dataclass
class Machine:
    """How the doctor looks at this computer. Tests build one with fakes, so they need no claude, gh or server."""

    which: Callable[[str], str | None] = shutil.which
    run: Runner = run_program
    fetch: net.Fetch = net.fetch
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    python: tuple[int, ...] = tuple(sys.version_info[:3])


@dataclass(frozen=True)
class Finding:
    """One line of the report: what was seen and, unless it is a PASS, how to fix it."""

    name: str
    status: str  # PASS, FAIL, WARN or SKIP
    detail: str
    fix: str = ""


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _why(exc: BaseException) -> str:
    """A short reason why a program gave no answer."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"no answer within {exc.timeout:g} s"
    if isinstance(exc, OSError):
        return exc.strerror or type(exc).__name__
    return type(exc).__name__


def _show(path: Path, width: int | None = None) -> str:
    """The path as a person types it (`~/...` inside the home folder); if too wide, its start is cut."""
    try:
        text = "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        text = str(path)
    return text if width is None or len(text) <= width else "..." + text[-(width - 3):]


def _http_url(value: str) -> str | None:
    """`scheme://host[:port][/path]` when `value` is a usable http(s) URL, else None.

    Credentials, query and fragment are dropped, so what is fetched is also safe to print.
    """
    try:
        parts = urllib.parse.urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}{f':{port}' if port else ''}{parts.path.rstrip('/')}"


def _switch_url(value: str) -> str:
    """argparse type for --switch-url: anything but an http(s) URL is a usage error (exit 2)."""
    if _http_url(value) is None:
        raise argparse.ArgumentTypeError(f"not an http(s) URL: {value!r} (example: http://127.0.0.1:52258)")
    return value


def _git_env() -> dict[str, str]:
    """git only reads: no index refresh (GIT_OPTIONAL_LOCKS=0) and never a password prompt."""
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


def _git(machine: Machine, git: str, repo: Path, *args: str) -> str | None:
    """stdout of `git -C repo ARGS`, or None when git fails, cannot start or is too slow."""
    try:
        proc = machine.run([git, "-C", str(repo), *args], timeout=TIMEOUT, env=_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _own_repo(machine: Machine, path: Path) -> bool:
    """Is `path` the top folder of a git repo of its own (not a plain folder inside some other repo)?"""
    if not path.is_dir():
        return False
    git = machine.which("git")
    if git is None:
        return (path / ".git").exists()
    top = (_git(machine, git, path, "rev-parse", "--show-toplevel") or "").strip()
    return bool(top) and Path(top).resolve() == path.resolve()


def _check_python(machine: Machine) -> Finding:
    """The Python that runs the checker: 3.11 or newer (rule files are read with tomllib)."""
    version = ".".join(str(part) for part in machine.python)
    if tuple(machine.python[:2]) < MIN_PYTHON:
        return Finding("python", FAIL, f"{version} is too old: Preflight needs 3.11 or newer", PYTHON_FIX)
    return Finding("python", PASS, version)


def _check_git(machine: Machine) -> Finding:
    git = machine.which("git")
    if git is None:
        return Finding("git", FAIL, "git is not on PATH", GIT_FIX)
    try:
        proc = machine.run([git, "--version"], timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Finding("git", FAIL, f"git does not start ({_why(exc)})", GIT_FIX)
    if proc.returncode != 0:
        return Finding("git", FAIL, f"git --version failed (exit {proc.returncode})", GIT_FIX)
    return Finding("git", PASS, _first_line(proc.stdout).removeprefix("git version ") or "on PATH")


def _check_claude(machine: Machine) -> Finding:
    """The Claude Code CLI: without it no agent can start on this machine."""
    claude = machine.which("claude")
    if claude is None:
        return Finding("claude", WARN, "Claude Code is not on PATH: agents on this machine cannot start", CLAUDE_FIX)
    try:
        proc = machine.run([claude, "--version"], timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Finding("claude", WARN, f"claude --version gave no answer ({_why(exc)})", CLAUDE_FIX)
    if proc.returncode != 0:
        return Finding("claude", WARN, f"claude --version failed (exit {proc.returncode})", CLAUDE_FIX)
    return Finding("claude", PASS, clip(_first_line(proc.stdout), 60) or "on PATH")


def _check_gh(machine: Machine) -> Finding:
    """The GitHub CLI and its sign-in. Only the exit code counts: the output names accounts and scopes."""
    needed = "only needed for private repos and pull requests"
    gh = machine.which("gh")
    if gh is None:
        return Finding("gh", WARN, f"the GitHub CLI is not installed: {needed}", "install gh (brew install gh), then gh auth login")
    try:
        proc = machine.run([gh, "auth", "status"], timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Finding("gh", WARN, f"gh auth status gave no answer ({_why(exc)}): {needed}", "run gh auth status yourself")
    if proc.returncode != 0:
        return Finding("gh", WARN, f"not signed in to GitHub, or GitHub did not answer: {needed}", "gh auth login")
    return Finding("gh", PASS, "signed in to GitHub")


def _check_layout(folder: Path, machine: Machine) -> Finding:
    """kit/pf and rules/ must be there (FAIL); project/ should be a git repo (WARN)."""
    problems: list[str] = []
    pf = folder / "kit" / "pf"
    if not pf.is_file():
        problems.append("kit/pf is missing")
    elif not os.access(pf, os.X_OK):
        problems.append("kit/pf is not executable")
    if not (folder / "rules").is_dir():
        problems.append("rules/ is missing")
    project = folder / "project"
    note = "" if _own_repo(machine, project) else ("project/ is not a git repo" if project.is_dir() else "there is no project/")
    if problems:
        detail = "; ".join([*problems, note] if note else problems)
        return Finding("layout", FAIL, detail, f"a person runs scripts/setup-demo.sh {_show(folder)}")
    if note:
        return Finding("layout", WARN, note, "run kit/pf target sample")
    return Finding("layout", PASS, "kit/pf, rules/ and a git project/ are in place")


def _check_kit(folder: Path, machine: Machine) -> Finding:
    """The checker copy must be the one that was cloned: no local changes."""
    kit = folder / "kit"
    if not kit.is_dir():
        return Finding("kit", SKIP, "there is no kit/ (see layout)", f"a person runs scripts/setup-demo.sh {_show(folder)}")
    git = machine.which("git")
    if git is None:
        return Finding("kit", SKIP, "git is not on PATH, so local changes cannot be seen", GIT_FIX)
    if not _own_repo(machine, kit):
        return Finding("kit", WARN, "kit/ is not a git clone, so local changes cannot be seen", f"a person runs scripts/setup-demo.sh {_show(folder)}")
    head = (_git(machine, git, kit, "rev-parse", "--short", "HEAD") or "").strip()
    at = f"HEAD {head}" if head else "no commit yet"
    status = _git(machine, git, kit, "status", "--porcelain")
    if status is None:
        return Finding("kit", WARN, f"git status failed in kit/ ({at})", KIT_FIX)
    changed = [line for line in status.splitlines() if line.strip()]
    if changed:
        return Finding("kit", WARN, f"the checker copy has {len(changed)} local change(s), {at}", KIT_FIX)
    return Finding("kit", PASS, f"no local changes, {at}")


def _matches_bash(matcher: object) -> bool:
    """Does a PreToolUse matcher cover the Bash tool? Empty or `*` covers every tool; anything else is a regex."""
    if matcher is None or matcher in ("", "*"):
        return True
    if not isinstance(matcher, str):
        return False
    try:
        return re.fullmatch(matcher, "Bash") is not None
    except re.error:
        return matcher == "Bash"


def _registers_hook(settings: Mapping[str, Any]) -> bool:
    """Does hooks.PreToolUse run kit/hooks/risky_git.py before Bash commands?"""
    hooks = settings.get("hooks")
    groups = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not _matches_bash(group.get("matcher")):
            continue
        entries = group.get("hooks")
        for entry in entries if isinstance(entries, list) else []:
            command = entry.get("command") if isinstance(entry, dict) else None
            if isinstance(command, str) and entry.get("type") == "command" and HOOK_SCRIPT in command:
                return True
    return False


def _allows_pf(settings: Mapping[str, Any]) -> bool:
    permissions = settings.get("permissions")
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    return isinstance(allow, list) and PF_RULE in allow


def _read_json(path: Path) -> tuple[Any, str | None]:
    """(data, None), or (None, why the file cannot be used)."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"cannot be read ({exc.strerror or type(exc).__name__})"
    except ValueError as exc:  # bad JSON or not UTF-8
        line = getattr(exc, "lineno", None)
        return None, "is not valid JSON" + (f" (line {line})" if line else "")


def _check_settings(folder: Path) -> Finding:
    """The permission file the agents run under: kit/pf allowed, the approval hook registered and not turned off."""
    path = folder / ".claude" / "settings.json"
    if not path.is_file():
        return Finding("settings", FAIL, ".claude/settings.json is missing", SETTINGS_FIX)
    data, why = _read_json(path)
    if why is None and not isinstance(data, dict):
        why = "is not a JSON object"
    if why is not None:
        return Finding("settings", FAIL, f".claude/settings.json {why}", SETTINGS_FIX)
    problems: list[str] = []
    if not _allows_pf(data):
        problems.append(f"{PF_RULE} is not allowed")
    if not _registers_hook(data):
        problems.append("no approval hook")
    if data.get("disableAllHooks") is True:
        problems.append("disableAllHooks turns every hook off")
    fix = SETTINGS_FIX if problems else "a person deletes disableAllHooks from .claude/settings.local.json"
    # setup-demo.sh never touches the local file, which Claude Code also reads: it must not turn the hook off
    local, _ = _read_json(folder / ".claude" / "settings.local.json")
    if isinstance(local, dict) and local.get("disableAllHooks") is True:
        problems.append("disableAllHooks in .claude/settings.local.json turns every hook off")
    if problems:
        return Finding("settings", FAIL, "; ".join(problems), fix)
    return Finding("settings", PASS, f"{PF_RULE} allowed, approval hook registered")


def _call_hook(machine: Machine, python: str, script: Path, folder: Path, command: str) -> subprocess.CompletedProcess[str] | str:
    """The hook's answer to one Bash tool call, sent on stdin the way Claude Code sends it, or why it gave none."""
    payload = {
        "session_id": "preflight-doctor",
        "cwd": str(folder),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    try:
        return machine.run(
            [python, str(script)],
            stdin_text=json.dumps(payload),
            timeout=TIMEOUT,
            cwd=folder,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(folder)},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _why(exc)


def _decision(stdout: str) -> str | None:
    """hookSpecificOutput.permissionDecision from the hook's JSON answer, if there is one."""
    try:
        answer = json.loads(stdout)
    except ValueError:
        return None
    specific = answer.get("hookSpecificOutput") if isinstance(answer, dict) else None
    return specific.get("permissionDecision") if isinstance(specific, dict) else None


def _check_hook(folder: Path, machine: Machine) -> Finding:
    """Run the approval hook as Claude Code does: it must ask before a hard reset and stay silent on `git status`."""
    script = folder / HOOK_SCRIPT
    if not script.is_file():
        fix = HOOK_FIX if (folder / "kit").is_dir() else f"a person runs scripts/setup-demo.sh {_show(folder)}"
        return Finding("hook", FAIL, f"{HOOK_SCRIPT} is missing", fix)
    python = machine.which("python3")
    if python is None:
        return Finding("hook", FAIL, "python3 is not on PATH, so Claude Code cannot run the hook", PYTHON_FIX)
    risky = _call_hook(machine, python, script, folder, RISKY_SAMPLE)
    if isinstance(risky, str):
        return Finding("hook", FAIL, f"gave no answer ({risky})", HOOK_FIX)
    if risky.returncode != 0 or _decision(risky.stdout) != "ask":
        exit_note = f" (exit {risky.returncode})" if risky.returncode else ""
        return Finding("hook", FAIL, f"did not ask before {RISKY_SAMPLE}{exit_note}", HOOK_FIX)
    safe = _call_hook(machine, python, script, folder, SAFE_SAMPLE)
    if isinstance(safe, str):
        return Finding("hook", FAIL, f"gave no answer ({safe})", HOOK_FIX)
    if safe.returncode != 0 or safe.stdout.strip():
        exit_note = f" (exit {safe.returncode})" if safe.returncode else ""
        return Finding("hook", FAIL, f"did not stay silent on {SAFE_SAMPLE}{exit_note}", HOOK_FIX)
    return Finding("hook", PASS, f"asks before {RISKY_SAMPLE}, silent on {SAFE_SAMPLE}")


def _check_rules(folder: Path) -> Finding:
    """The approved check list must load; its fingerprint is the one `kit/pf sha` prints."""
    path = folder / "rules" / "approved.toml"
    if not path.is_file():
        waiting = " (rules/draft.toml is waiting)" if (folder / "rules" / "draft.toml").is_file() else ""
        return Finding("rules", WARN, f"rules/approved.toml is missing{waiting}", RULES_FIX)
    ruleset, problems = parse_ruleset(path)
    if ruleset is None:
        errors = [p for p in problems if p.level == "ERROR"]
        first = f": {clip(f'{errors[0].where}: {errors[0].message}', 120)}" if errors else ""
        fix = "run kit/pf lint --rules rules/approved.toml; @scout fixes it and the Captain approves it again"
        return Finding("rules", FAIL, f"rules/approved.toml has {len(errors)} error(s){first}", fix)
    return Finding("rules", PASS, f"{ruleset.name} v{ruleset.version}, {len(ruleset.checks)} checks, sha256:{ruleset.sha256[:12]}")


def _check_switch(machine: Machine, url: str | None, source: str) -> Finding:
    """GET <url>/health with a short timeout; only asked when a Switch URL was given."""
    if not url:
        return Finding("switch", SKIP, "no Switch URL given, so Switch was not asked", SWITCH_HINT)
    base = _http_url(url)
    if base is None:
        return Finding("switch", FAIL, f"{source} is not an http(s) URL", SWITCH_HINT)
    health = base + "/health"
    try:
        response = machine.fetch(health, timeout=HEALTH_TIMEOUT, max_bytes=4096)
    except net.NetError as exc:
        return Finding("switch", FAIL, f"{health} did not answer ({exc.reason})", SWITCH_FIX)
    if response.status != 200:
        return Finding("switch", FAIL, f"{health} answered HTTP {response.status}", SWITCH_FIX)
    return Finding("switch", PASS, f"{health} answered 200")


def diagnose(folder: Path, machine: Machine | None = None, switch_url: str | None = None) -> list[Finding]:
    """Run the ten checks in order. A check that crashes becomes a FAIL that says so; the others still run.

    Without `switch_url` the SWITCH_API_URL environment variable is used; without either, switch is a SKIP.
    """
    machine = machine or Machine()
    source = "--switch-url" if switch_url else "SWITCH_API_URL"
    url = switch_url or machine.env.get("SWITCH_API_URL", "").strip() or None
    steps: list[tuple[str, Callable[[], Finding]]] = [
        ("python", lambda: _check_python(machine)),
        ("git", lambda: _check_git(machine)),
        ("claude", lambda: _check_claude(machine)),
        ("gh", lambda: _check_gh(machine)),
        ("layout", lambda: _check_layout(folder, machine)),
        ("kit", lambda: _check_kit(folder, machine)),
        ("settings", lambda: _check_settings(folder)),
        ("hook", lambda: _check_hook(folder, machine)),
        ("rules", lambda: _check_rules(folder)),
        ("switch", lambda: _check_switch(machine, url, source)),
    ]
    findings: list[Finding] = []
    for name, step in steps:
        try:
            finding = step()
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            if machine.env.get("PREFLIGHT_DEBUG"):
                raise
            finding = Finding(name, FAIL, f"the check crashed ({type(exc).__name__}: {exc})", "run it again with PREFLIGHT_DEBUG=1 and report the error")
        detail = redact(" ".join(finding.detail.split())) or "(no detail)"
        findings.append(Finding(finding.name, finding.status, detail, finding.fix))
    return findings


def summary_line(findings: Sequence[Finding]) -> str:
    """`DOCTOR: OK` when nothing failed or warned, else `DOCTOR: <n> problem(s), <m> warning(s)`."""
    problems = sum(1 for f in findings if f.status == FAIL)
    warnings = sum(1 for f in findings if f.status == WARN)
    if not problems and not warnings:
        return "DOCTOR: OK"
    return f"DOCTOR: {problems} problem(s), {warnings} warning(s)"


def format_table(findings: Sequence[Finding], folder: Path) -> str:
    """The report for a person at a terminal: one row per check, the fix under it, at most 100 columns."""
    title = "PREFLIGHT DOCTOR  folder: "
    lines = [title + _show(folder, TABLE_WIDTH - len(title))]
    num_w = len(str(len(findings)))
    name_w = max([len("CHECK"), *(len(f.name) for f in findings)])
    head = f" {'#':>{num_w}}  {'CHECK':<{name_w}}  {'STATUS':<6}  "
    lines.append(head + "DETAIL")
    width = max(TABLE_WIDTH - len(head), 20)
    for n, finding in enumerate(findings, start=1):
        prefix = f" {n:>{num_w}}  {finding.name:<{name_w}}  {finding.status:<6}  "
        text = textwrap.wrap(finding.detail, width, break_on_hyphens=False) or [""]
        if finding.status != PASS and finding.fix:
            text += textwrap.wrap(f"-> fix: {finding.fix}", width, subsequent_indent="   ", break_on_hyphens=False)
        lines.append(prefix + text[0])
        lines.extend(" " * len(prefix) + part for part in text[1:])
    lines.append(summary_line(findings))
    return "\n".join(lines)


def _chat_line(finding: Finding, width: int) -> str:
    """`STATUS name: detail  -> fix: ...` in at most `width` characters; the detail is cut before the fix."""
    head = f"{finding.status} {finding.name}: "
    tail = f"  -> fix: {finding.fix}" if finding.status != PASS and finding.fix else ""
    room = width - len(head) - len(tail)
    if room < 20:  # a very long fix: cut the whole line instead
        return clip(head + finding.detail + tail, width)
    return head + clip(finding.detail, room) + tail


def format_chat(findings: Sequence[Finding], folder: Path, limit: int = CHAT_LIMIT) -> str:
    """Compact lines for one Discord message, at most `limit` characters, ending with the summary line.

    Too long lines are cut first; if it still does not fit, the last lines are left out and a
    `(+N more ...)` line says so.
    """
    title = "PREFLIGHT DOCTOR  "
    head = title + _show(folder, 100 - len(title))
    footer = summary_line(findings)
    for width in (CHAT_LINE_MAX, 110):
        text = "\n".join([head, *(_chat_line(f, width) for f in findings), footer])
        if len(text) <= limit:
            return text
    lines = [_chat_line(f, 70) for f in findings]
    for shown in range(len(lines), -1, -1):
        more = [f"(+{len(lines) - shown} more, run with --format table)"] if shown < len(lines) else []
        text = "\n".join([head, *lines[:shown], *more, footer])
        if len(text) <= limit:
            return text
    return text[:limit]


def cmd_doctor(args: argparse.Namespace, machine: Machine | None = None) -> int:
    """Print the report. Exit 0 when nothing failed, 1 when something did, 2 when the folder does not exist."""
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"preflight: error: folder not found: {folder}", file=sys.stderr)
        return 2
    folder = folder.resolve()
    findings = diagnose(folder, machine, args.switch_url)
    printer = format_chat if args.format == "chat" else format_table
    print(printer(findings, folder))
    return 1 if any(f.status == FAIL for f in findings) else 0


def add_parser(sub: Any) -> None:
    parser = sub.add_parser("doctor", help="check this machine and the agents' shared folder before a run")
    parser.add_argument(
        "--folder", default=".", metavar="DIR", help="the shared folder with kit/, rules/, project/ and .claude/ (default: .)"
    )
    parser.add_argument(
        "--switch-url", type=_switch_url, metavar="URL",
        help="Switch's local API, like http://127.0.0.1:52258 (default: the SWITCH_API_URL environment variable)",
    )
    parser.add_argument("--format", choices=("table", "chat"), default="table")
    parser.set_defaults(func=cmd_doctor)
