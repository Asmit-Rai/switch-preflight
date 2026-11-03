#!/usr/bin/env python3
"""Claude Code PreToolUse hook: make risky shell commands ask a person first.

Why a hook: a permission rule such as `Bash(git reset:*)` only matches commands that START with
`git reset`. Agents write `git -C /path/project reset --hard x`, `cd project && git reset ...` or
`FOO=1 git filter-branch ...`, and none of those match. This script reads the command itself, so the
decision does not depend on how the command is written. The model proposes, code decides.

What it does: Claude Code sends the tool call as JSON on stdin. If the command is risky, the script
answers {"permissionDecision": "ask"}. Claude Code then asks the person (in a Switch room, that is
a permission card with Allow once / Allow for this session / Decline). Anything else is left alone
(no output, exit 0), so the normal permission rules still apply.

Risky means:
  * rewriting or throwing away git history or work: reset, rebase, filter-branch, filter-repo,
    commit --amend, update-ref, replace, reflog expire/delete, gc --prune, clean, stash drop/clear,
    restore, checkout/switch with --force, branch -D, tag -d
  * sending work away or pointing it elsewhere: push, remote add/set-url
  * deleting files: rm, rmdir, unlink, shred, trash, git rm, find ... -delete
  * running downloaded code: curl or wget piped into a shell

Limits (stated plainly): this is a guard for an agent that is trying to follow the rules, not a
sandbox. It reads the command text. It cannot see inside a script file the agent wrote, inside
`python3 -c` code that calls git by itself, or inside an alias. The agent's own permission rules
and the person's answers still apply on top of it.

Standard library only. Any input it does not understand is left alone (never blocks work by error).
"""

from __future__ import annotations

import json
import re
import sys

# A git command: `git`, any number of global options (-C path, -c k=v, --no-pager ...), then the
# subcommand. The options may contain spaces only through the forms below.
_GIT = r"(?:^|[\s;&|(`/'\"])git(?:\s+(?:-C\s+\S+|-c\s+\S+|--git-dir[=\s]\S+|--work-tree[=\s]\S+|-[A-Za-z]|--[a-z][a-z-]*(?:=\S+)?))*\s+"

# (reason, regex) pairs. The regex is matched against the whole command string, case-sensitively.
_RISKY: list[tuple[str, re.Pattern[str]]] = [
    ("rewrites git history or moves a branch (reset)", re.compile(_GIT + r"reset\b")),
    ("rewrites git history (rebase)", re.compile(_GIT + r"rebase\b")),
    ("rewrites git history (filter-branch or filter-repo)", re.compile(_GIT + r"filter-(?:branch|repo)\b")),
    ("rewrites the last commit (amend)", re.compile(_GIT + r"commit\b[^;&|]*--amend\b")),
    ("moves a git ref by hand (update-ref)", re.compile(_GIT + r"update-ref\b")),
    ("replaces git objects (replace)", re.compile(_GIT + r"replace\b")),
    ("deletes git history entries (reflog expire or delete)", re.compile(_GIT + r"reflog\s+(?:expire|delete)\b")),
    ("deletes unreachable git objects (gc --prune)", re.compile(_GIT + r"gc\b[^;&|]*--prune\b")),
    ("deletes untracked files (git clean)", re.compile(_GIT + r"clean\b")),
    ("throws away stashed work (stash drop or clear)", re.compile(_GIT + r"stash\s+(?:drop|clear)\b")),
    ("throws away working-tree changes (git restore)", re.compile(_GIT + r"restore\b")),
    ("overwrites local changes (forced checkout or switch)", re.compile(_GIT + r"(?:checkout|switch)\b[^;&|]*(?:\s-f\b|--force\b|--discard-changes\b)")),
    ("deletes a branch (branch -D or --delete --force)", re.compile(_GIT + r"branch\b[^;&|]*(?:\s-D\b|--delete\b[^;&|]*(?:--force|-f)\b|(?:--force|-f)\b[^;&|]*--delete\b)")),
    ("deletes a tag", re.compile(_GIT + r"tag\b[^;&|]*(?:\s-d\b|--delete\b)")),
    ("sends work to another repository (push)", re.compile(_GIT + r"push\b")),
    ("changes where the repository sends work (remote add or set-url)", re.compile(_GIT + r"remote\s+(?:add|set-url|rename|remove|rm)\b")),
    ("deletes files from git (git rm)", re.compile(_GIT + r"rm\b")),
    ("deletes files (rm, rmdir, unlink, shred or trash)", re.compile(r"(?:^|[\s;&|(`/'\"])(?:sudo\s+)?(?:rm|rmdir|unlink|shred|trash)(?=\s|$)")),
    ("deletes files (find ... -delete)", re.compile(r"(?:^|[\s;&|(`/'\"])find\b[^;&|]*\s-delete\b")),
    ("runs downloaded code (curl or wget piped into a shell)", re.compile(r"(?:curl|wget)\b[^;&]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|python3?|node)\b")),
]


def risky_reason(command: str) -> str | None:
    """Why this shell command needs a person, or None when it is fine to leave to the normal rules."""
    if not isinstance(command, str) or not command.strip():
        return None
    # A heredoc or quoted argument may mention "git reset" as plain text (a commit message, a README).
    # Strip the bodies of heredocs and quoted strings so only the real command words are matched.
    candidates = [_strip_text(command)]
    # `bash -c '...'` and `eval "..."` run a string as shell code, so the quoted text IS the command.
    if _RUNS_A_STRING.search(command):
        candidates.append(command)
    for text in candidates:
        for reason, pattern in _RISKY:
            if pattern.search(text):
                return reason
    return None


_RUNS_A_STRING = re.compile(r"(?:^|[\s;&|(`/'\"])(?:(?:ba|z|da|k|c|a)?sh\b[^;&|\n]*\s-[A-Za-z]*c\b|eval\b)")


def _strip_text(command: str) -> str:
    """Remove heredoc bodies and the inside of quoted strings; keep the shell words around them."""
    # Heredoc: <<'EOF' ... EOF  (also <<EOF and <<-EOF). Keep the `<<EOF` marker, drop the body.
    command = re.sub(
        r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)",
        "<<HEREDOC",
        command,
        flags=re.DOTALL,
    )
    # Quoted strings: keep the quotes, drop what is inside. A double-quoted string that holds a
    # command substitution ($( ... ) or backticks) is real shell code, so it stays visible.
    command = re.sub(r"'[^']*'", "''", command)

    def blank_double(match: re.Match[str]) -> str:
        inner = match.group(0)[1:-1]
        if "$(" in inner or "`" in inner:
            return match.group(0)
        return '"' + " " * len(inner) + '"'

    return re.sub(r'"(?:\\.|[^"\\])*"', blank_double, command)


def decide(payload: object) -> dict | None:
    """The hook's answer for one tool call, or None to stay silent."""
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    reason = risky_reason(command) if isinstance(command, str) else None
    if reason is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"Preflight rule: this {reason}. A person must approve it.",
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # not a tool call we understand: do nothing, never block work by mistake
    answer = decide(payload)
    if answer is not None:
        print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
