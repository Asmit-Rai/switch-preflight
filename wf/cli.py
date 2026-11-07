"""The wf command line: `./wf <command>` in a project folder (or `python3 -m wf <command>` from inside it).

There is no option to point wf at another folder: the hook reads `./wf ship` from the command text,
and an option between `wf` and `ship` must never hide a ship from the permission card.

Exit codes: 0 done, 1 error (environment or bug), 2 refused (a rule), 3 gate closed.
Every command prints the chat form; `--json` prints {"code", "text", "task"} instead.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from . import chat
from .config import Config, load_config
from .errors import WfError

COMMANDS = (
    "init", "doctor", "new", "status", "next", "skill", "spec submit", "change", "cancel", "plan submit",
    "build start", "build done", "run", "commit", "review approve", "review changes", "test start", "test item",
    "test done", "ship", "verify", "ask", "answer", "stop", "resume", "log", "note",
)

# command -> (module, function). Imported when called, so each part of wf loads only what it needs.
HANDLERS = {
    "new": ("cmd_task", "cmd_new"),
    "status": ("cmd_task", "cmd_status"),
    "next": ("cmd_task", "cmd_next"),
    "skill": ("cmd_task", "cmd_skill"),
    "spec submit": ("cmd_task", "cmd_spec_submit"),
    "change": ("cmd_task", "cmd_change"),
    "cancel": ("cmd_task", "cmd_cancel"),
    "plan submit": ("cmd_task", "cmd_plan_submit"),
    "ask": ("cmd_task", "cmd_ask"),
    "answer": ("cmd_task", "cmd_answer"),
    "stop": ("cmd_task", "cmd_stop"),
    "resume": ("cmd_task", "cmd_resume"),
    "log": ("cmd_task", "cmd_log"),
    "note": ("cmd_task", "cmd_note"),
    "build start": ("cmd_build", "cmd_build_start"),
    "build done": ("cmd_build", "cmd_build_done"),
    "run": ("cmd_build", "cmd_run"),
    "commit": ("cmd_build", "cmd_commit"),
    "review approve": ("cmd_check", "cmd_review"),
    "review changes": ("cmd_check", "cmd_review"),
    "test start": ("cmd_check", "cmd_test_start"),
    "test item": ("cmd_check", "cmd_test_item"),
    "test done": ("cmd_check", "cmd_test_done"),
    "ship": ("cmd_ship", "cmd_ship"),
    "verify": ("cmd_ship", "cmd_verify"),
    "doctor": ("doctor", "cmd_doctor"),
}


def _leaf(sub: argparse._SubParsersAction, name: str, command: str, help_text: str) -> argparse.ArgumentParser:
    parser = sub.add_parser(name, help=help_text, description=help_text)
    parser.add_argument("--json", action="store_true", help="print {code, text, task} as JSON (for scripts and tests)")
    parser.set_defaults(command=command)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./wf", description="The build room's workflow tool. The model proposes, code decides.",
    )
    sub = parser.add_subparsers(dest="top", required=True, metavar="<command>")

    p = _leaf(sub, "init", "init", "set up a project folder for wf (run once, from the kit)")
    p.add_argument("folder", nargs="?", default=".", help="the project folder (a git repository)")
    p.add_argument("--main", help="the branch work starts from (default: main)")
    p.add_argument("--install", help="the install command for a clean clone (default: from the project)")
    p.add_argument("--suite", action="append", help="a suite command (repeat for several; default: from the project)")
    p.add_argument("--ports", help="ports the project uses, like 4321,4399")
    p.add_argument("--rules", help="a Preflight ruleset, relative to the kit; the ship gate runs it")

    _leaf(sub, "doctor", "doctor", "check the setup and say what to fix")

    p = _leaf(sub, "new", "new", "start a task from a person's words")
    p.add_argument("goal")
    p.add_argument("--by", required=True, help="who asked for it")

    p = _leaf(sub, "status", "status", "where the task is; with --for: whose turn it is")
    p.add_argument("--for", dest="for_alias", metavar="ALIAS")

    _leaf(sub, "next", "next", "print the hand-off packet for the current stage")

    p = _leaf(sub, "skill", "skill", "print an agent's skill")
    p.add_argument("alias")

    spec = sub.add_parser("spec", help="spec submit").add_subparsers(dest="action", required=True, metavar="submit")
    p = _leaf(spec, "submit", "spec submit", "submit the Done means items and the Assumed line (stdin or --file)")
    p.add_argument("--file")

    for name, what in (("change", "send the task back to spec with a person's words"), ("cancel", "end the task")):
        p = _leaf(sub, name, name, what)
        p.add_argument("quote", help="the person's words")
        p.add_argument("--by", required=True)

    plan = sub.add_parser("plan", help="plan submit").add_subparsers(dest="action", required=True, metavar="submit")
    p = _leaf(plan, "submit", "plan submit", "submit the steps (stdin or --file)")
    p.add_argument("--file")

    build = sub.add_parser("build", help="build start | build done").add_subparsers(dest="action", required=True, metavar="start|done")
    _leaf(build, "start", "build start", "make the task branch and commit the records")
    _leaf(build, "done", "build done", "check the build and hand it to review")

    p = _leaf(sub, "run", "run", "run a step's test, a fix's test or the suite, and keep the output")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--step", type=int)
    which.add_argument("--fix", type=int)
    which.add_argument("--suite", action="store_true")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--red", action="store_true", help="it must fail")
    mode.add_argument("--green", action="store_true", help="it must pass")
    p.add_argument("cmd", nargs="?", help="the command to run (only with --fix)")

    p = _leaf(sub, "commit", "commit", "commit the named files with the step's or fix's subject")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--step", type=int)
    which.add_argument("--fix", type=int)
    p.add_argument("--test", action="store_true", help="this is the test commit")
    p.add_argument("-m", "--message", required=True, help="what it checks or adds")
    p.add_argument("files", nargs="+")

    review = sub.add_parser("review", help="review approve | review changes").add_subparsers(dest="action", required=True, metavar="approve|changes")
    for name in ("approve", "changes"):
        p = _leaf(review, name, f"review {name}", f"record the review verdict: {name}")
        p.add_argument("--findings", help="the findings as text (else stdin)")
        p.add_argument("--file")

    test = sub.add_parser("test", help="test start | test item | test done").add_subparsers(dest="action", required=True, metavar="start|item|done")
    _leaf(test, "start", "test start", "make a clean clone at the reviewed commit and install")
    p = _leaf(test, "item", "test item", "record PASS or FAIL for one item")
    p.add_argument("n", type=int)
    p.add_argument("result", help="PASS or FAIL")
    p.add_argument("--evidence", required=True, help="one line you saw")
    _leaf(test, "done", "test done", "write the test report and hand on")

    p = _leaf(sub, "ship", "ship", "run the gate, then push and open the pull request (or merge)")
    p.add_argument("--confirmed-by", dest="confirmed_by")
    p.add_argument("--quote")

    for name, what in (("verify", "print the proof chain of a task"), ("log", "the timeline of a task"), ("note", "the closing note of a task")):
        p = _leaf(sub, name, name, what)
        p.add_argument("task", nargs="?")

    p = _leaf(sub, "ask", "ask", "ask a person a blocking question")
    p.add_argument("question")
    p.add_argument("--default", required=True)

    p = _leaf(sub, "answer", "answer", "record a person's answer")
    p.add_argument("--by", required=True)
    p.add_argument("--quote", required=True)

    p = _leaf(sub, "stop", "stop", "pause the task")
    p.add_argument("--by")
    _leaf(sub, "resume", "resume", "go on, and print the hand-off again")
    return parser


def _snapshot(project: Path) -> dict | None:
    try:
        from .context import Ctx
        from .state import Store

        return Ctx.last(Store(project / ".workflow").load())
    except Exception:  # noqa: BLE001 - the snapshot is only extra information
        return None


def run(argv: Sequence[str], project: Path | None = None, stdin: str | None = None) -> tuple[int, str]:
    """Run one wf command and return (exit code, text). Never raises for a command's own failure."""
    argv = list(argv)
    words = {command.split()[0] for command in COMMANDS}
    if argv and argv[0] not in words and argv[0] not in ("-h", "--help"):
        # Nothing may stand before the command word: the hook reads `./wf ship` from the command text,
        # so `./wf -- ship` or `./wf --x ship` must never run a ship without the permission card.
        return 2, f"Say the command first: ./wf <command>. {argv[0]!r} is not a wf command."
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return (exc.code if isinstance(exc.code, int) else 2), ""
    folder = Path(project or Path.cwd()).resolve()
    command = args.command
    try:
        if command == "init":
            from .install import cmd_init

            code, text = 0, cmd_init(args, Path.cwd())
        else:
            from .context import make_ctx

            if command in ("skill", "doctor"):
                try:
                    config = load_config(folder)
                except WfError:
                    config = Config(project=folder)
                if command == "skill":
                    from .cmd_task import cmd_skill
                    from .context import Ctx
                    from .gitops import Git
                    from .state import Store

                    ctx = Ctx(project=folder, config=config, store=Store(folder / ".workflow"), git=Git(folder), stdin=stdin)
                    return 0, cmd_skill(ctx, args)
                ctx = make_ctx(folder, stdin, config=config)
            else:
                ctx = make_ctx(folder, stdin)
            module, name = HANDLERS[command]
            handler = getattr(importlib.import_module(f".{module}", __package__), name)
            code, text = 0, handler(ctx, args)
    except WfError as exc:
        code, text = exc.code, str(exc)
    except KeyboardInterrupt:
        return 130, "Interrupted. Nothing half-written: run ./wf status."
    except Exception as exc:  # noqa: BLE001 - a crash must be an error the agent can report, never a pass
        if os.environ.get("WF_DEBUG"):
            raise
        code, text = 1, f"wf: internal error: {type(exc).__name__}: {exc}. Say so in the room and stop (run with WF_DEBUG=1 to see where)."
    text = chat.fit(text)
    if getattr(args, "json", False):
        return code, json.dumps({"code": code, "text": text, "task": _snapshot(folder)}, ensure_ascii=False, indent=1)
    return code, text


def _stopped(signum: int, frame: object) -> None:
    raise KeyboardInterrupt  # so every `finally` runs: the ship token goes, a step's processes are stopped


def main(argv: Sequence[str] | None = None, project: Path | None = None) -> int:
    signal.signal(signal.SIGTERM, _stopped)
    code, text = run(sys.argv[1:] if argv is None else argv, project=project)
    if text:
        print(text)
    return code
