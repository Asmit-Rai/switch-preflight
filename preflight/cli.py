"""Command line: run, verdict, pack, lint, sha.

Exit codes: 0 ok, 1 not ready / gate closed / lint errors, 2 no verdict could be produced
(usage error, bad ruleset, or an internal error).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import gitutil
from .checks import Context, run_checks
from .htmlreport import render_html, write_html
from .model import READY, Ruleset
from .pack import build_pack, read_meta, write_pack
from .report import format_chat, format_json, format_table
from .ruleset import RulesetError, load_ruleset, parse_ruleset
from .timeutil import now_iso
from .verdict import DEFAULT_MAX_AGE_MIN, build_document, check_gate, parse_approved_sha, verdict_path, write_json


def _error(message: str) -> int:
    print(f"preflight: error: {message}", file=sys.stderr)
    return 2


def _approved_sha(value: str) -> str:
    """argparse type for --approved-sha: a bad value is a usage error (exit 2) with a clear message."""
    try:
        return parse_approved_sha(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _load_ruleset(path: str) -> Ruleset | None:
    """Load a ruleset. On errors print every one to stderr and return None; the caller then exits 2."""
    try:
        return load_ruleset(path)
    except RulesetError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        _error(f"{path} is not a valid ruleset (run `preflight lint --rules {path}`)")
        return None


def cmd_run(args: argparse.Namespace) -> int:
    project = Path(args.project)
    if not project.is_dir():
        return _error(f"project folder not found: {project}")
    project = project.resolve()
    ruleset = _load_ruleset(args.rules)
    if ruleset is None:
        return 2

    only = None
    if args.only:
        only = {part.strip() for part in args.only.split(",") if part.strip()}
        unknown = sorted(only - {c.id for c in ruleset.checks})
        if unknown:
            return _error(f"--only names unknown check id(s): {', '.join(unknown)}")

    results = run_checks(ruleset, Context(project, offline=args.offline), only)
    # Commit and dirtiness are read after the checks: a check (like the tests) may touch the tree.
    commit = gitutil.head_sha(project)
    try:
        dirty = bool(gitutil.dirty_entries(project))
    except gitutil.GitError:
        dirty = False
    doc = build_document(ruleset, project, commit, dirty, results, now_iso())

    try:
        if not args.no_write:
            write_json(doc, verdict_path(project))
        if args.json:
            write_json(doc, Path(args.json))
    except OSError as exc:
        return _error(f"cannot write the verdict file: {exc}")
    if args.html:
        meta_path = project / "preflight.toml"
        try:
            meta = read_meta(meta_path) if meta_path.is_file() else {}
        except (OSError, ValueError):
            meta = {}  # only the optional facts are lost; `pack` reports a broken file
        try:
            write_html(Path(args.html), render_html(doc, project, meta))
        except OSError as exc:
            return _error(f"cannot write {args.html}: {exc}")

    printers = {"table": format_table, "chat": format_chat, "json": format_json}
    print(printers[args.format](doc))
    if doc["verdict"] == READY and dirty:
        print("NOTE: the working tree has uncommitted changes, so `preflight verdict` stays closed until they are committed.", file=sys.stderr)
    return 0 if doc["verdict"] == READY else 1


APPROVED_SHA_HELP = (
    "stay closed unless the verdict was produced by the ruleset whose sha256 starts with HEX "
    "(8 to 64 hex digits, see the `sha` command)"
)


def cmd_verdict(args: argparse.Namespace) -> int:
    gate = check_gate(args.project, args.max_age_min, approved_sha=args.approved_sha)
    print(gate.message)
    return 0 if gate.ok else 1


def cmd_pack(args: argparse.Namespace) -> int:
    project = Path(args.project)
    meta: dict = {}
    meta_path = Path(args.meta) if args.meta else project / "preflight.toml"
    if args.meta and not meta_path.is_file():
        return _error(f"--meta file not found: {meta_path}")
    if meta_path.is_file():
        try:
            meta = read_meta(meta_path)
        except (OSError, ValueError) as exc:
            return _error(f"cannot read {meta_path}: {exc}")

    gate = check_gate(project, args.max_age_min, approved_sha=args.approved_sha)
    print(gate.message)
    if not gate.ok or gate.document is None:
        print("PACK REFUSED: the gate is closed, so no pack was written.")
        return 1
    out = Path(args.out)
    root = project.resolve()
    try:
        write_pack(out, build_pack(root, gate.document, meta))
    except OSError as exc:
        return _error(f"cannot write {out}: {exc}")
    print(f"PACK WRITTEN: {out}")
    if args.html:
        try:
            write_html(Path(args.html), render_html(gate.document, root, meta, gate.message, args.approved_sha))
        except OSError as exc:
            return _error(f"cannot write {args.html}: {exc}")
        print(f"HTML WRITTEN: {args.html}")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    ruleset, problems = parse_ruleset(args.rules)
    for problem in problems:
        print(problem)
    errors = sum(1 for p in problems if p.level == "ERROR")
    if not problems and ruleset is not None:
        print(f"OK: {ruleset.name} v{ruleset.version}, {len(ruleset.checks)} checks, no problems")
    return 1 if errors else 0


def cmd_sha(args: argparse.Namespace) -> int:
    """Print the short (12 hex) fingerprint of a ruleset: what the Captain approves in chat."""
    ruleset = _load_ruleset(args.rules)
    if ruleset is None:
        return 2
    print(ruleset.sha256[:12])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m preflight",
        description="Deterministic pre-submission checks. The model proposes, code decides.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run every check in a ruleset against a project")
    run.add_argument("--project", required=True, help="project folder (a git repo)")
    run.add_argument("--rules", required=True, help="ruleset TOML file")
    run.add_argument("--json", metavar="OUT", help="also write the verdict JSON to this file")
    run.add_argument("--html", metavar="OUT", help="also write a self-contained HTML report of this run to this file")
    run.add_argument("--format", choices=("table", "chat", "json"), default="table")
    run.add_argument("--offline", action="store_true", help="skip every check that needs the network")
    run.add_argument("--only", metavar="ID,ID", help="run only these checks; the others count as SKIP")
    run.add_argument("--no-write", action="store_true", help="do not write .preflight/verdict.json")
    run.set_defaults(func=cmd_run)

    verdict = sub.add_parser("verdict", help="the gate: is the last run READY for this exact commit?")
    verdict.add_argument("--project", required=True)
    verdict.add_argument("--max-age-min", type=float, default=DEFAULT_MAX_AGE_MIN)
    verdict.add_argument("--approved-sha", type=_approved_sha, metavar="HEX", help=APPROVED_SHA_HELP)
    verdict.set_defaults(func=cmd_verdict)

    pack = sub.add_parser("pack", help="write the submission pack (refuses while the gate is closed)")
    pack.add_argument("--project", required=True)
    pack.add_argument("--out", required=True, help="Markdown file to write")
    pack.add_argument("--html", metavar="OUT", help="also write the HTML report to this file (only while the gate is open)")
    pack.add_argument("--meta", metavar="preflight.toml", help="optional [project] facts (name, team, ...)")
    pack.add_argument("--max-age-min", type=float, default=DEFAULT_MAX_AGE_MIN)
    pack.add_argument("--approved-sha", type=_approved_sha, metavar="HEX", help=APPROVED_SHA_HELP)
    pack.set_defaults(func=cmd_pack)

    lint = sub.add_parser("lint", help="validate a ruleset")
    lint.add_argument("--rules", required=True)
    lint.set_defaults(func=cmd_lint)

    sha = sub.add_parser("sha", help="print a ruleset's fingerprint (first 12 hex characters of its sha256)")
    sha.add_argument("--rules", required=True)
    sha.set_defaults(func=cmd_sha)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - a crash must exit 2, never pass for a verdict
        if os.environ.get("PREFLIGHT_DEBUG"):
            raise
        print(f"preflight: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
