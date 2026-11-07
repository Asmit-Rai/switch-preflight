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

With the flag `--deny` it answers {"permissionDecision": "deny"} instead, with a reason that says what
to do (use another way, or say which command was needed in one message and stop). Use it where nobody
watches for permission cards: an unanswered card is a silent hang, a refusal is an answer.

With `--policy wf` (or `--policy=wf`) it guards a wf build room, where agents run `./wf <command>` and
only `./wf commit` may commit and only `./wf ship` may push or merge. Two tiers. Deny: everything risky
below, plus git switch/checkout, add, commit, cherry-pick, revert, am, every stash form, merge, pull,
and npm/pnpm/yarn publish, each with a reason that names the ./wf command to use. Ask (a permission
card): `./wf ship` only, so a person's click is what lets a task ship. Deny wins over ask, and `--deny`
changes nothing under this policy. Any other --policy value is ignored: the default rules apply.

Risky means:
  * rewriting or throwing away git history or work: reset, rebase, filter-branch, filter-repo,
    commit --amend, update-ref, replace, reflog expire/delete, gc --prune, clean, stash drop/clear,
    restore, checkout/switch with --force, branch -D, tag -d
  * sending work away or pointing it elsewhere: push, remote add/set-url
  * deleting files: rm, rmdir, unlink, shred, trash, git rm, find ... -delete
  * running downloaded code: curl or wget piped into a shell
  * running scripts/setup-demo.sh or scripts/reset-demo.sh: they replace the project under test and
    the agents' permission file (reading them with cat, sed or grep is free)
  * switching the project under test: `pf target ...` or `python3 -m preflight target ...` (only
    `--show` is free: it changes nothing)
  * acting on GitHub as the person who signed in: any `gh` command that is not read-only (pr create,
    merge, comment, review; issue create, close; repo create, fork, delete, edit; release create;
    workflow run; secret set; api writes ...). Read-only means gh --version, gh help, gh auth status,
    gh search, gh status, gh <noun> view|list|status|diff|checks|watch, and gh api with no
    -X/--method other than GET and none of -f, -F, --field, --raw-field, --input

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
    # Run as the command itself, by a shell (sh SCRIPT), or sourced; a path in `cat SCRIPT` is only read.
    ("replaces the project or the agents' permission file (setup-demo.sh or reset-demo.sh)", re.compile(
        r"(?:(?:^|[;&|(`\n])\s*(?:\w+=\S*\s+)*(?:(?:command|exec|env)\s+(?:\w+=\S*\s+)*)?"
        r"|(?:^|[\s;&|(`/'\"])(?:ba|z|da|k|c|a)?sh\s+(?:-\S+\s+)*"
        r"|(?:^|[\s;&|(`])(?:source|\.)\s+)"
        r"(?:\S*/)?(?:setup|reset)-demo\.sh(?=[\s;&|)`'\"]|$)"
    )),
]


def risky_reason(command: str) -> str | None:
    """Why this shell command needs a person, or None when it is fine to leave to the normal rules."""
    if not isinstance(command, str) or not command.strip():
        return None
    # A heredoc or quoted argument may mention "git reset" as plain text (a commit message, a README).
    # Strip the bodies of heredocs and quoted strings so only the real command words are matched.
    texts = [_strip_text(command)]
    raw: list[str] = []
    # `bash -c '...'` and `eval "..."` run a string as shell code, so the quoted text IS the command.
    if _RUNS_A_STRING.search(command):
        raw.append(command)
        texts += _inner_commands(command)
    for text in texts + raw:
        for reason, pattern in _RISKY:
            if pattern.search(text):
                return reason
    # The rules with an exception (pf target --show, read-only gh) read stripped text only: in the raw
    # text a quote can hide where one command ends, as in `bash -c 'kit/pf target x' --show`.
    for text in texts:
        for rule in _RULES:
            reason = rule(text)
            if reason is not None:
                return reason
    return None


_RUNS_A_STRING = re.compile(r"(?:^|[\s;&|(`/'\"])(?:(?:ba|z|da|k|c|a)?sh\b[^;&|\n]*\s-[A-Za-z]*c\b|eval\b)")


# Heredoc: <<'EOF' ... EOF  (also <<EOF and <<-EOF).
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", re.DOTALL)
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def _strip_text(command: str) -> str:
    """Remove heredoc bodies and the inside of quoted strings; keep the shell words around them."""
    # Keep the `<<EOF` marker, drop the body.
    command = _HEREDOC.sub("<<HEREDOC", command)
    # Quoted strings: keep the quotes, drop what is inside. A double-quoted string that holds a
    # command substitution ($( ... ) or backticks) is real shell code, so it stays visible.
    command = re.sub(r"'[^']*'", "''", command)

    def blank_double(match: re.Match[str]) -> str:
        inner = match.group(0)[1:-1]
        if "$(" in inner or "`" in inner:
            return match.group(0)
        # Empty quotes, like a single-quoted string: a quoted option value (git -C "/a b" push) must stay
        # one word, or the git rules lose the subcommand behind it.
        return '""'

    return re.sub(r'"(?:\\.|[^"\\])*"', blank_double, command)


def _inner_commands(command: str) -> list[str]:
    """The code that `bash -c '...'` or `eval "..."` runs, stripped like a command of its own.

    Quotes come off one level at a time (for a shell in a shell). Each quoted string is fenced with `;`,
    so the words after it (`bash -c '...' --show`) are not read as part of the command inside it.
    """
    found: list[str] = []
    text = _HEREDOC.sub("<<HEREDOC", command)
    for _ in range(3):
        inner = _QUOTED.sub(_unquote, text)
        if inner == text:
            break
        found.append(_strip_text(inner))
        text = inner
    return found


def _unquote(match: re.Match[str]) -> str:
    quoted = match.group(0)
    body = quoted[1:-1] if quoted[0] == "'" else re.sub(r'\\(["\\$`])', r"\1", quoted[1:-1])
    return f" ; {body} ; "


# `kit/pf target` or `python3 -m preflight target`; the words after it are read by _switches_target.
_PF_TARGET = re.compile(r"(?:(?:^|[\s;&|(`/'\"])pf|(?:^|\s)-[A-Za-z]*m\s*preflight)\s+target(?![^\s;&|)`'\"])")
# The gh command itself (also /opt/homebrew/bin/gh); its words are read by _acts_on_github.
_GH = re.compile(r"(?:^|[\s;&|(`/'\"])gh(?![^\s;&|)`])")
_GH_READ_VERBS = frozenset({"view", "list", "status", "diff", "checks", "watch"})
_COMMENT = re.compile(r"(?:^|\s)#")


def _words_after(text: str, start: int, stops: str) -> str:
    """The rest of one command: from START up to the first character in STOPS, without a # comment."""
    end = start
    while end < len(text) and text[end] not in stops:
        end += 1
    return _COMMENT.split(text[start:end], maxsplit=1)[0]


def _switches_target(text: str) -> str | None:
    """`pf target SOURCE` replaces the project under test; `pf target --show` only reads it."""
    for match in _PF_TARGET.finditer(text):
        # A quote also ends the words read here: in `bash -c 'kit/pf target x' --show` the --show is bash's.
        rest = _words_after(text, match.end(), ";&|\n)`'\"")
        if not re.search(r"(?:^|\s)--show(?=\s|$)", rest):
            return "switches the project under test (pf target)"
    return None


def _acts_on_github(text: str) -> str | None:
    """A gh command that is not read-only acts on GitHub as the person who signed in."""
    for match in _GH.finditer(text):
        words = [w for w in re.split(r"[\s<>]+", _words_after(text, match.end(), ";&|\n)`")) if w]
        label = _gh_write(words)
        if label is not None:
            return f"acts on GitHub as you ({label})"
    return None


def _gh_write(words: list[str]) -> str | None:
    """`gh WORDS` named by its first two words when it may change something on GitHub; None when read-only.

    A word we cannot read (a quoted string, a variable) is never one of the read-only words, so it asks.
    """
    if not words or words[0] in ("help", "search", "status") or words in (["--version"], ["version"], ["--help"], ["-h"]):
        return None
    if words[0] == "api":
        method = _gh_api_write(words[1:])
        return None if method is None else f"gh api {method}".rstrip()
    if len(words) > 1 and not words[0].startswith("-") and words[1] in _GH_READ_VERBS:
        return None
    return "gh " + " ".join(words[:2])


def _gh_api_write(args: list[str]) -> str | None:
    """The method of a `gh api` call that may write, else None.

    It writes with -X/--method other than GET, or with -f, -F, --field, --raw-field or --input (which
    make gh send POST unless a method is given). An unreadable method (quoted) counts as a write.
    """
    methods: list[str] = []
    data = False
    for i, word in enumerate(args):
        following = args[i + 1] if i + 1 < len(args) else ""
        if word.startswith("--"):
            name, eq, value = word[2:].partition("=")
            if name in ("field", "raw-field", "input"):
                data = True
            elif name == "method":
                methods.append(value if eq else following)
        elif word.startswith("-"):
            for pos, flag in enumerate(word[1:], start=1):  # short flags may be grouped: -iX POST
                if flag in "fF":
                    data = True
                    break
                if flag == "X":
                    methods.append(word[pos + 1:].lstrip("=") or following)
                    break
                if flag in "Hpqt":  # these take a value: the rest of the word is that value
                    break
    methods = [m.strip("'\"").upper() for m in methods]
    writes = [m for m in methods if m != "GET"]
    if writes:
        return writes[0]
    if data:
        return methods[0] if methods else "POST"
    return None


_RULES = (_switches_target, _acts_on_github)


# --policy wf: in a wf build room only `./wf commit` commits and only `./wf ship` pushes or merges
# (docs/WF.md, section 7). These patterns are
# refused on top of everything risky above. The subcommand must end where the pattern ends, so longer
# words such as `git merge-base` or `git checkout-index` are not caught.
_WF_END = r"(?![\w-])"
_WF_DENY: list[tuple[str, re.Pattern[str]]] = [
    ("switches branches: ./wf build start puts the builder on the task branch, and the tester works in the clean "
     "clone from ./wf test start", re.compile(_GIT + r"(?:switch|checkout)" + _WF_END)),
    ('stages files outside wf: use ./wf commit --step <k> -m "<what>" -- <files>', re.compile(_GIT + r"add" + _WF_END)),
    ("makes a commit outside wf: use ./wf commit", re.compile(_GIT + r"(?:commit|cherry-pick|revert|am)" + _WF_END)),
    ("stashes work, which makes a dirty tree look clean: commit with ./wf commit instead",
     re.compile(_GIT + r"stash" + _WF_END)),
    ("merges branches: only ./wf ship merges, after a person clicks Allow once",
     re.compile(_GIT + r"(?:merge|pull)" + _WF_END)),
    # npm, pnpm or yarn, any global options (with or without a value), then publish. `yarn npm publish` too.
    ("publishes a package", re.compile(
        r"(?:^|[\s;&|(`/'\"])(?:npm|pnpm|yarn)(?:\s+-\S+(?:\s+[^\s;&|()`'\"-]\S*)?)*\s+publish" + _WF_END)),
]
# `./wf ship`, `/abs/kit/wf ship`, `wf ship` or `python3 -m wf ship`: the one command that asks a person.
_WF_SHIP = (
    re.compile(r"(?:^|[\s;&|(`/'\"])(?:\S*/)?wf\s+ship" + _WF_END),
    re.compile(r"-m\s*wf(?:\.__main__|\.cli)?\s+ship" + _WF_END),
)
# Read on the raw command too: `./wf "ship"`, `./wf 'ship'` or options between `wf` and `ship` must still ask.
# Only option words may stand between them, so a commit message such as -m "ship it" never asks.
_WF_SHIP_RAW = re.compile(
    r"(?:^|[\s;&|(`/'\"])(?:\S*/)?wf[\"']?(?:\s+(?:--?[A-Za-z][\w-]*(?:=\S+|\s+[^\s-]\S*)?|--))*\s+[\"']?ship[\"']?"
    + _WF_END
)
_WF_SHIP_MODULE = re.compile(r"-m\s*[\"']?wf(?:\.__main__|\.cli)?[\"']?\s+[\"']?ship[\"']?" + _WF_END)
_WF_SHIP_REASON = "ships the task (./wf ship): it pushes the branch and opens the pull request, or merges into main"


def _stripped_texts(command: str) -> list[str]:
    """The stripped texts risky_reason reads: the command without heredoc bodies and quoted strings, and
    the code inside `bash -c '...'` or `eval "..."` (stripped the same way) when the command runs a string."""
    texts = [_strip_text(command)]
    if _RUNS_A_STRING.search(command):
        texts += _inner_commands(command)
    return texts


def wf_decision(command: str) -> tuple[str, str] | None:
    """The wf room's answer for one shell command: ("deny", reason), ("ask", reason) or None.

    Deny: everything risky_reason finds, and git work done around wf (_WF_DENY). Ask: `./wf ship`.
    Deny wins over ask, so `./wf ship && git push` is refused, not put to a person.
    """
    if not isinstance(command, str) or not command.strip():
        return None
    reason = risky_reason(command)
    if reason is not None:
        return ("deny", reason)
    texts = _stripped_texts(command)
    for text in texts:
        for wf_reason, pattern in _WF_DENY:
            if pattern.search(text):
                return ("deny", wf_reason)
    if (any(pattern.search(text) for text in texts for pattern in _WF_SHIP)
            or _WF_SHIP_RAW.search(command) or _WF_SHIP_MODULE.search(command)):
        return ("ask", _WF_SHIP_REASON)
    return None


def decide(payload: object, deny: bool = False, policy: str | None = None) -> dict | None:
    """The hook's answer for one tool call, or None to stay silent.

    By default a risky command asks a person. With deny=True (the `--deny` flag) it is refused at
    once, with a reason the agent can act on: for a room where nobody watches for permission cards,
    an unanswered card is a silent hang. With policy="wf" (the `--policy wf` flag) the wf room's two
    tiers apply instead (wf_decision) and `deny` changes nothing. Any other policy is ignored.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if policy == "wf":
        return _wf_answer(command)
    reason = risky_reason(command) if isinstance(command, str) else None
    if reason is None:
        return None
    if deny:
        decision = "deny"
        text = (
            f"Preflight rule: this {reason}. In this room nobody can click an approval card, so it was not run. "
            "Do not retry it. Use a way that does not do this (for example a new clone, or git switch --detach). "
            "If there is none, say which command you needed in one message in the room, then stop."
        )
    else:
        decision = "ask"
        text = f"Preflight rule: this {reason}. A person must approve it."
    return _answer(decision, text)


def _wf_answer(command: object) -> dict | None:
    """decide() under --policy wf."""
    found = wf_decision(command) if isinstance(command, str) else None
    if found is None:
        return None
    decision, reason = found
    if decision == "deny":
        text = (
            f"wf rule: this {reason}. It was not run. Do not retry it or look for a way around it. "
            "If you cannot go on without it, run ./wf ask 'the command you needed, in plain words' "
            "--default 'what you do instead, without that command', post what it prints, then stop."
        )
    else:
        text = f"wf rule: this {reason}. A person must approve it."
    return _answer(decision, text)


def _answer(decision: str, text: str) -> dict:
    """The shape Claude Code reads from a PreToolUse hook."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": text,
        }
    }


def _policy(args: list[str]) -> str | None:
    """"wf" for `--policy wf` or `--policy=wf`; None when no policy is given or the value is not one we know."""
    for i, arg in enumerate(args):
        if arg == "--policy":
            value = args[i + 1] if i + 1 < len(args) else ""
        elif arg.startswith("--policy="):
            value = arg.split("=", 1)[1]
        else:
            continue
        if value == "wf":
            return value
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    deny = "--deny" in args  # any other flag is ignored: never block work by a typo
    policy = _policy(args)  # an unknown --policy value is ignored the same way: the default rules apply
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # not a tool call we understand: do nothing, never block work by mistake
    answer = decide(payload, deny=deny, policy=policy)
    if answer is not None:
        print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
