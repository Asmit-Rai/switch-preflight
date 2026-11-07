"""The ship gate (spec section 10.2) and the proof chain. `ship`, `verify` and `status --for shipper`
all call `gate()`, so the ready line before the click and the ship after it use one answer."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import specplan
from .chat import count, numbered, short
from .cmd_build import commit_marks, commit_shape_problems, evidence_problems, steps_of
from .context import Ctx
from .machine import short_sha

KIT = Path(__file__).resolve().parent.parent


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    preflight: str = "not configured"
    preflight_file: str | None = None


def _numbers(found: list[int]) -> str:
    return ", ".join(map(str, found))


def gate(ctx: Ctx, task: dict, *, run_preflight: bool = True) -> GateResult:
    reasons: list[str] = []
    tid, head = task["id"], task["head"]
    if task["stage"] != "ship":
        reasons.append(f"{tid} is at {task['stage']}, not at ship")
    branch = ctx.git.branch()
    if branch != task["branch"]:
        reasons.append(f"the project folder is on {branch or 'a detached HEAD'}, not on {task['branch']}")
    now_head = ctx.git.head()
    if not head:
        reasons.append("build done is not recorded")
    elif now_head != head:
        reasons.append(f"HEAD moved since build done (was {short_sha(head)}, now {short_sha(now_head)})")
    entries = ctx.clean_entries(task)
    if entries:
        reasons.append(f"the tree is not clean: {', '.join(entries[:3])}")
    review = task["review"]
    if not (review and review["verdict"] == "APPROVE" and review["head"] == head):
        reasons.append(f"no review APPROVE at {short_sha(head)}")
    tests = task["tests"]
    numbers = [item["n"] for item in task["spec"]["items"]] if task["spec"] else []
    if not head or tests["head"] != head:
        reasons.append(f"no test run at {short_sha(head)}")
    else:
        not_pass = [n for n in numbers if tests["items"].get(str(n), {}).get("result") != "PASS"]
        if not_pass:
            reasons.append(f"item{'s' if len(not_pass) > 1 else ''} {_numbers(not_pass)} not PASS")
    reasons += evidence_problems(ctx, task)
    suite = task["build"]["suite"]
    if not (head and suite and suite["sha"] == head and suite["clean"]):
        reasons.append(f"no green suite at {short_sha(head)} on a clean tree")
    result = GateResult(ok=False, reasons=reasons)
    if ctx.config.rules:
        if reasons or not run_preflight:
            result.preflight = "not run"
        else:
            result.preflight, result.preflight_file, detail = _preflight(ctx, task, head)
            if result.preflight != "READY":
                reasons.append(f"Preflight is {result.preflight}: {detail}")
    result.ok = not reasons
    return result


def _preflight(ctx: Ctx, task: dict, head: str) -> tuple[str, str | None, str]:
    """Run the Preflight checker on the project: ("READY" or why not, evidence file, detail)."""
    rules = ctx.config.rules_path()
    if rules is None or not rules.is_file():
        return "missing", None, f"the ruleset {rules} is not there"
    kit = ctx.config.kit or KIT
    pf = kit / "pf"
    if not pf.is_file():
        return "missing", None, f"{pf} is not there"
    out = ctx.task_dir(task["id"]) / "evidence" / f"preflight-{head[:7]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(pf), "run", "--project", str(ctx.project), "--rules", str(rules),
               "--format", "chat", "--no-write", "--json", str(out)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=ctx.config.run_timeout, cwd=str(ctx.project), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "not finished", None, f"pf run took longer than {ctx.config.run_timeout:.0f}s"
    file = ctx.relative(out) if out.is_file() else None
    if proc.returncode == 0:
        return "READY", file, ""
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode == 1:
        detail = next((line for line in lines if "FAIL" in line), lines[0] if lines else "see the evidence file")
        return "NOT READY", file, short(detail, 120)
    errors = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
    return "an error", file, short(errors[-1] if errors else f"pf exit {proc.returncode}", 120)


def ready_line(task: dict, result: GateResult) -> str:
    total = len(task["spec"]["items"])
    preflight = f"Preflight {result.preflight}" if result.preflight != "not configured" else "Preflight not configured"
    items = "item" if total == 1 else "items"
    return (f"{task['id']} is ready to ship: review APPROVE at {short_sha(task['head'])}, "
            f"{total} of {total} {items} PASS, {preflight}.")


def closed_line(result: GateResult) -> str:
    more = f" (and {len(result.reasons) - 1} more)" if len(result.reasons) > 1 else ""
    return f"Gate closed: {result.reasons[0]}{more}."


def shipped_where(task: dict, main: str) -> str:
    ship = task["ship"] or {}
    if ship.get("url"):
        return ship["url"]
    if ship.get("merge_sha"):
        return f"merged into {main} as {short_sha(ship['merge_sha'])}"
    return "pushed"


def proof_chain(ctx: Ctx, task: dict, result: GateResult | None) -> list[str]:
    """The whole chain in plain lines: spec, plan, commits, evidence, review, tests, Preflight, and the end."""
    tid, head = task["id"], task["head"]
    lines = [f"{tid} proof chain: {short(task['goal'], 100)}"]
    spec = task["spec"]
    if spec:
        text = ctx.git.show(head, f"{ctx.records(task)}/spec.md") if head else None
        if text is None:
            where = ""
        elif specplan.spec_sha(text) == spec["sha256"]:
            where = f"; spec.md at {short_sha(head)} matches"
        else:
            where = f"; spec.md at {short_sha(head)} does NOT match"
        lines.append(f"Spec: {count(len(spec['items']), 'item')}, sha256 {spec['sha256'][:12]}{where}")
    else:
        lines.append("Spec: not submitted yet")
    steps = steps_of(task)
    if steps:
        served = sorted({n for step in steps for n in step.items})
        verb = "serves" if len(steps) == 1 else "serve"
        lines.append(f"Plan: {count(len(steps), 'step')} {verb} {numbered('item', served)}")
    else:
        lines.append("Plan: not submitted yet")
    if head:
        commits = ctx.git.commits(task["base"], head)
        problems = commit_shape_problems(task, commits)
        marks = commit_marks(commits)
        tested = [str(s.k) for s in steps if s.runs and s.k in marks["step test"]]
        if problems:
            lines.append(f"Commits: {len(commits)} on {task['branch']}, but {problems[0]}")
        else:
            lines.append(f"Commits: {len(commits)} on {task['branch']}: records first, then a test commit before the code"
                         + (f" for {numbered('step', tested)}" if tested else ""))
        both = [str(s.k) for s in steps if s.runs and {"red", "green"} <= set(task["build"]["evidence"].get(str(s.k), {}))]
        suite = task["build"]["suite"]
        suite_text = (f"suite green at {short_sha(suite['sha'])}" + (" (clean tree)" if suite["clean"] else " (tree not clean)")
                      if suite else "no suite run")
        lines.append(f"Evidence: red and green for {numbered('step', both) if both else 'no step'}; {suite_text}")
    else:
        lines.append("Commits: build not done yet")
    review = task["review"]
    lines.append(f"Review: {review['verdict']} at {short_sha(review['head'])}" if review else "Review: none yet")
    tests = task["tests"]
    if tests["items"] and tests["head"]:
        total = len(task["spec"]["items"])
        passed = sum(1 for r in tests["items"].values() if r["result"] == "PASS")
        lines.append(f"Tests: {passed} of {total} PASS at {short_sha(tests['head'])} (clean clone)")
    else:
        lines.append("Tests: none yet")
    if task["stage"] == "done":
        ship = task["ship"] or {}
        lines.append(f"Preflight: {ship.get('preflight', 'not configured')}")
        lines.append(f"Shipped at {short_sha(ship.get('sha'))}: {short(shipped_where(task, ctx.config.main), 120)}")
    elif task["stage"] == "cancelled":
        lines.append("Cancelled: nothing shipped.")
    elif result is not None:
        lines.append(f"Preflight: {result.preflight}")
        lines.append("Gate open." if result.ok else closed_line(result))
    return lines
