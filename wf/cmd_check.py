"""The reviewer's and the tester's commands (plan Task 2): review approve|changes, test start|item|done.

Both record their result at the commit `build done` recorded and refuse when HEAD moved, so the
reviewed commit is the tested commit is the shipped commit.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import evidence
from .chat import bounded, short
from .context import Ctx
from .machine import next_line, short_sha
from .packets import waiting_line
from .timeutil import now

MAX_FINDINGS = 10


def _loop(ctx: Ctx, task: dict, source: str, label: str, summary: str, details: list[str]) -> None:
    """Back to build for a fix round; at the loop limit a person decides (spec section 6.2)."""
    task["loops"] += 1
    task["fix_round"] += 1
    task["stage"] = "build"
    task["fix_reason"] = {"from": source, "label": label, "summary": summary, "details": details}
    if task["loops"] >= ctx.config.max_loops:
        task["waiting"] = {"question": summary, "default": "continue", "stage": "build", "asked_at": now(), "kind": "loops"}


def _after_loop(ctx: Ctx, task: dict) -> str:
    return waiting_line(task) if task["waiting"] else f"Next: build (fix {task['fix_round']})"


# ---------------------------------------------------------------- review


def cmd_review(ctx: Ctx, args) -> str:
    verdict = "APPROVE" if args.command == "review approve" else "CHANGES"
    text = args.findings if args.findings is not None else ctx.read_input(args.file)
    findings = [short(line, 300) for line in text.splitlines() if line.strip()][:MAX_FINDINGS]
    state = ctx.load()
    task = ctx.check(state, "review")
    head = ctx.require_head(task, "review")
    if verdict == "CHANGES" and not findings:
        raise ctx.refuse(task, "review", "Not now: say what to change: the findings on stdin, or --findings \"<text>\".")
    with ctx.store.transaction() as st:
        task = ctx.check(st, "review")
        task["review"] = {"verdict": verdict, "head": head, "findings": findings, "at": now()}
        if verdict == "APPROVE":
            task["stage"] = "test"
        else:
            _loop(ctx, task, "review", f"review: CHANGES NEEDED at {head[:7]}", findings[0], findings)
    ctx.log(task, f"review {args.command.split()[-1]}")
    if verdict == "APPROVE":
        return "\n".join([f"REVIEW {task['id']}: APPROVE at {head[:7]}", *bounded(findings, 1100, "./wf log"), "Next: test"])
    head_line = f"REVIEW {task['id']}: CHANGES NEEDED at {head[:7]} (loop {task['loops']} of {ctx.config.max_loops})"
    return "\n".join([head_line, *bounded(findings, 1000, "./wf status --for builder"), _after_loop(ctx, task)])


def reviewer_turn(ctx: Ctx, task: dict) -> str:
    head, base = short_sha(task["head"]), short_sha(task["base"])
    files = []
    for step in task["plan"]["steps"]:
        if step["file"] != "-" and step["file"] not in files:
            files.append(step["file"])
    lines = [f"Your turn: review {task['id']} on {task['branch']} at {head} against {ctx.records(task)}/spec.md."]
    if task["fix_round"]:
        lines.append(f"This is a review after fix round {task['fix_round']}: read the fix commits first.")
    lines.append(f"Tests first: git diff {base}..{head} -- {' '.join(files[:6])}")
    lines.append(f"Then the code: git diff {base}..{head} --stat, and each file against the items.")
    lines.append('Then: ./wf review approve --findings "<one line>", or ./wf review changes with the findings on stdin.')
    return "\n".join(lines)


# ---------------------------------------------------------------- test


def clone_root(ctx: Ctx, task: dict) -> Path:
    """Where the tester's clean clones go: /tmp/wf/<project>-<hash>/T<n>, or under WF_TMPDIR."""
    base = Path(os.environ.get("WF_TMPDIR") or "/tmp")
    tag = hashlib.sha256(str(ctx.project).encode("utf-8")).hexdigest()[:8]
    return base / "wf" / f"{ctx.project.name}-{tag}" / task["id"]


def cmd_test_start(ctx: Ctx, args) -> str:
    state = ctx.load()
    task = ctx.check(state, "test start")
    head = ctx.require_head(task, "test start")
    root = clone_root(ctx, task)
    root.mkdir(parents=True, exist_ok=True)
    number = 1
    while (root / f"clone-{number}").exists():
        number += 1
    clone = root / f"clone-{number}"
    ctx.git.run("clone", "--quiet", "--no-hardlinks", "--branch", task["branch"], str(ctx.project), str(clone))
    from .gitops import Git

    clone_head = Git(clone).head()
    if clone_head != head:
        raise ctx.refuse(task, "test start", f"Not now: the clone is at {clone_head[:7]}, not at {head[:7]}. Run ./wf test start again.")
    install = ctx.config.install or ("npm ci" if (clone / "package-lock.json").is_file() else "")
    record = {"command": install, "ok": True, "exit": 0, "file": None}
    if install:
        result = evidence.execute(install, clone, ctx.config.run_timeout)
        path = evidence.write(ctx.task_dir(task["id"]) / "evidence" / f"install-{number}.txt",
                              evidence.header(result, clone, head) + result.output)
        record = {"command": install, "ok": result.exit == 0 and not result.timed_out, "exit": result.exit,
                  "file": path.relative_to(ctx.task_dir(task["id"])).as_posix()}
        shown = evidence.digest(result.output, limit=2, failed=result.exit != 0)
    with ctx.store.transaction() as st:
        task = ctx.check(st, "test start")
        task["tests"] = {"clone": str(clone), "install": record, "head": head, "items": {}, "result": None, "report": None}
    ctx.log(task, "test start")
    if not install:
        install_line = "Install: nothing to install."
    elif record["ok"]:
        install_line = f"Install: {short(install, 80)} passed (exit 0)."
    else:
        first = short(shown[0], 100) if shown else f"exit {record['exit']}"
        install_line = (f"Install FAILED: {short(install, 60)} exit {record['exit']}: {first}. "
                        "Report the items that need it as FAIL with that line.")
    items = len(task["spec"]["items"])
    return "\n".join([
        f"{task['id']} clean clone at {head[:7]}: {clone}",
        install_line,
        f"Work in it: cd {clone} && <command>. Run ./wf from {ctx.project}.",
        f"Test every item ({items} in all), then: ./wf test item <n> PASS|FAIL --evidence \"<one line you saw>\"",
    ])


def cmd_test_item(ctx: Ctx, args) -> str:
    result = str(args.result).strip().upper()
    state = ctx.load()
    task = ctx.check(state, "test item")
    if result not in ("PASS", "FAIL"):
        raise ctx.refuse(task, "test item", "Say PASS or FAIL.")
    proof = " | ".join(line.strip() for line in str(args.evidence).splitlines() if line.strip())
    if not proof:
        raise ctx.refuse(task, "test item", 'Give one line of evidence you produced: --evidence "<command and what it printed>".')
    if len(proof) > 400:
        raise ctx.refuse(task, "test item", "Keep the evidence to one line under 400 characters.")
    numbers = [item["n"] for item in task["spec"]["items"]]
    if args.n not in numbers:
        raise ctx.refuse(task, "test item", f"Not now: there is no item {args.n}: the items are 1 to {len(numbers)}.")
    ctx.require_head(task, "test item")
    with ctx.store.transaction() as st:
        task = ctx.check(st, "test item")
        task["tests"]["items"][str(args.n)] = {"result": result, "evidence": proof, "at": now()}
    ctx.log(task, f"test item {args.n}", detail=result)
    reported = task["tests"]["items"]
    missing = [n for n in numbers if str(n) not in reported]
    hint = f"Next: ./wf test item {missing[0]}, then ./wf test done" if missing else "Next: ./wf test done"
    return f"Item {args.n}: {result} recorded ({len(reported)} of {len(numbers)} reported).\n{hint}"


def cmd_test_done(ctx: Ctx, args) -> str:
    state = ctx.load()
    task = ctx.check(state, "test done")
    head = ctx.require_head(task, "test done")
    items = task["spec"]["items"]
    results = task["tests"]["items"]
    failed = [item["n"] for item in items if results[str(item["n"])]["result"] == "FAIL"]
    passed = len(items) - len(failed)
    install = task["tests"]["install"] or {}
    lines = [f"# {task['id']} test report at {head[:7]}", "",
             f"Clean clone: {task['tests']['clone']} (install: {install.get('command') or 'none'}, exit {install.get('exit', 0)})", ""]
    for item in items:
        found = results[str(item["n"])]
        lines += [f"{item['n']}. {found['result']}: {item['text']} [{item['check']}]", f"   Evidence: {found['evidence']}"]
    lines += ["", f"Result: {passed} of {len(items)} PASS"]
    report = evidence.write(ctx.task_dir(task["id"]) / "test-report.md", "\n".join(lines) + "\n")
    with ctx.store.transaction() as st:
        task = ctx.check(st, "test done")
        task["tests"]["result"] = "FAIL" if failed else "PASS"
        task["tests"]["report"] = report.relative_to(ctx.task_dir(task["id"])).as_posix()
        if failed:
            word = "item" if len(failed) == 1 else "items"
            details = [f"item {n}: {results[str(n)]['evidence']}" for n in failed]
            _loop(ctx, task, "test", f"test: {word} {', '.join(map(str, failed))} FAIL at {head[:7]}", details[0], details)
        else:
            task["stage"] = "ship"
    ctx.log(task, "test done", detail=f"{passed} of {len(items)} PASS")
    out = [f"Test {task['id']}: {passed} of {len(items)} PASS at {head[:7]}"]
    out += bounded([f"FAIL item {n}: {short(results[str(n)]['evidence'], 140)}" for n in failed], 900,
                   f".workflow/tasks/{task['id']}/test-report.md")
    out.append(_after_loop(ctx, task) if failed else "Next: ship")
    return "\n".join(out)


def tester_turn(ctx: Ctx, task: dict) -> str:
    items = [item["n"] for item in task["spec"]["items"]]
    tests = task["tests"]
    lines = [f"Your turn: test {task['id']} at {short_sha(task['head'])}: {len(items)} items ({ctx.records(task)}/spec.md)."]
    if not tests["clone"]:
        lines.append("Clone: not made yet. Next: ./wf test start")
    else:
        missing = [str(n) for n in items if str(n) not in tests["items"]]
        lines.append(f"Clone: {tests['clone']} (work in it: cd <clone> && <command>; run ./wf from {ctx.project})")
        lines.append(f"Reported: {len(items) - len(missing)} of {len(items)}." + (f" Still to report: {', '.join(missing)}." if missing else ""))
    lines.append('Then: ./wf test item <n> PASS|FAIL --evidence "<one line you saw>" for each item, and ./wf test done.')
    return "\n".join(lines)


__all__ = ["cmd_review", "cmd_test_start", "cmd_test_item", "cmd_test_done", "reviewer_turn", "tester_turn", "next_line"]
