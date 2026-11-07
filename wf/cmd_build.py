"""The builder's commands (plan Task 2): build start, run, commit, build done.

The order red, test commit, green, code commit is code here, not a promise: `commit` refuses a test
commit before a red run and a code commit before its test commit and a green run, and `build done`
checks the whole history of the branch.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import evidence, specplan
from .chat import bounded, count, numbered, short
from .context import Ctx
from .errors import Refused, WfError
from .gitops import Commit, GitError
from .machine import next_line, short_sha
from .state import empty_tests
from .timeutil import now

# The same list as wf/githooks.py BUILD_DIRS (a test keeps them equal): output that never goes into git.
BUILD_DIRS = ("dist", "build", "out", "coverage", "test-results", "playwright-report")
STEP_TEST = re.compile(r"^step (\d+) test: \S")
STEP_CODE = re.compile(r"^step (\d+): \S")
FIX_TEST = re.compile(r"^fix (\d+) test: \S")
FIX_CODE = re.compile(r"^fix (\d+): \S")
MAX_MESSAGE = 100


# ---------------------------------------------------------------- shared helpers


def steps_of(task: dict) -> list[specplan.Step]:
    return [specplan.step_from(record) for record in task["plan"]["steps"]] if task["plan"] else []


def evidence_dir(ctx: Ctx, task: dict) -> Path:
    return ctx.task_dir(task["id"]) / "evidence"


def records_subject(task: dict) -> str:
    return f"{task['id']}: spec and plan"


def commit_marks(commits: list[Commit]) -> dict[str, dict[int, int]]:
    """For each commit shape, the index of the first commit of each step or fix number."""
    marks: dict[str, dict[int, int]] = {"step test": {}, "step": {}, "fix test": {}, "fix": {}}
    for index, commit in enumerate(commits):
        for name, pattern in (("step test", STEP_TEST), ("step", STEP_CODE), ("fix test", FIX_TEST), ("fix", FIX_CODE)):
            match = pattern.match(commit.subject)
            if match:
                marks[name].setdefault(int(match.group(1)), index)
                break
    return marks


def commit_shape_problems(task: dict, commits: list[Commit]) -> list[str]:
    """Everything wrong with the history of the task branch (an empty list when it is right)."""
    problems: list[str] = []
    steps = steps_of(task)
    planned = {step.k for step in steps}
    if not commits or commits[0].subject != records_subject(task):
        problems.append(f'the first commit on {task["branch"]} is not "{records_subject(task)}"')
    for commit in commits[1:] if commits and commits[0].subject == records_subject(task) else commits:
        if len(commit.parents) > 1:
            problems.append(f"{commit.sha[:7]} is a merge commit: only ./wf commit commits go on the branch")
            continue
        subject = commit.subject
        step = STEP_TEST.match(subject) or STEP_CODE.match(subject)
        fix = FIX_TEST.match(subject) or FIX_CODE.match(subject)
        if step and int(step.group(1)) not in planned:
            problems.append(f"{commit.sha[:7]}: step {step.group(1)} is not in the plan")
        elif fix and not 1 <= int(fix.group(1)) <= task["fix_round"]:
            problems.append(f"{commit.sha[:7]}: fix {fix.group(1)} is not a fix round of this task")
        elif not step and not fix:
            problems.append(f'{commit.sha[:7]} "{short(subject, 60)}" is not a wf commit: only ./wf commit commits go on the branch')
    marks = commit_marks(commits)
    for step in steps:
        k = step.k
        if k not in marks["step"]:
            problems.append(f"step {k} has no code commit (step {k}: ...)")
        if step.runs:
            if k not in marks["step test"]:
                problems.append(f"step {k} has no test commit (step {k} test: ...)")
            elif k in marks["step"] and marks["step"][k] < marks["step test"][k]:
                problems.append(f"step {k}: the code commit comes before the test commit")
    for r in range(1, task["fix_round"] + 1):  # every fix round needs its test commit, then its fix commit
        if r not in marks["fix test"]:
            problems.append(f"fix {r} has no test commit (fix {r} test: ...)")
        if r not in marks["fix"]:
            problems.append(f"fix {r} has no fix commit (fix {r}: ...)")
        elif r in marks["fix test"] and marks["fix test"][r] > marks["fix"][r]:
            problems.append(f"fix {r}: the fix commit comes before its test commit")
    return problems


def evidence_problems(ctx: Ctx, task: dict) -> list[str]:
    """Steps with a command that lack a red or a green run (or whose evidence file is gone)."""
    problems: list[str] = []
    for step in steps_of(task):
        if not step.runs:
            continue
        found = task["build"]["evidence"].get(str(step.k), {})
        for kind in ("red", "green"):
            entry = found.get(kind)
            if entry is None:
                problems.append(f"step {step.k} has no {kind} run (./wf run --step {step.k} --{kind})")
            elif not (ctx.task_dir(task["id"]) / entry["file"]).is_file():
                problems.append(f"the {kind} evidence of step {step.k} is missing ({entry['file']})")
    return problems


def _step(task: dict, k: int) -> specplan.Step:
    for step in steps_of(task):
        if step.k == k:
            return step
    total = len(steps_of(task))
    raise Refused(f"There is no step {k}: the plan has steps 1 to {total}.")


def _evidence_entry(ctx: Ctx, task: dict, path: Path, result: evidence.RunResult, head: str) -> dict:
    return {"file": path.relative_to(ctx.task_dir(task["id"])).as_posix(), "exit": result.exit, "sha": head,
            "at": now(), "command": result.command}


# ---------------------------------------------------------------- build start


def cmd_build_start(ctx: Ctx, args) -> str:
    state = ctx.load()
    task = ctx.check(state, "build start")
    if task["build"]["started"]:
        ctx.ensure_on_branch(task, "build start")
        return f"{task['id']} build already started on {task['branch']}.\n{next_line(task, ctx.alias)}"
    ctx.require_clean(task, "build start")
    tid, branch, base = task["id"], task["branch"], task["base"]
    if not ctx.git.rev(base):
        raise ctx.refuse(task, "build start", f"Not now: the base commit {short_sha(base)} of {tid} is gone. A person must look at it.")
    folder = ctx.project / ctx.records(task)
    names = [f"{ctx.records(task)}/{name}" for name in ("goal.md", "plan.md", "spec.md")]
    for name in names:
        if not (ctx.project / name).is_file():
            raise ctx.refuse(task, "build start", f"Not now: {name} is missing. Tell the manager: change, to write the spec again.")
    spec_text = (folder / "spec.md").read_text(encoding="utf-8")
    if specplan.spec_sha(spec_text) != task["spec"]["sha256"]:
        raise ctx.refuse(task, "build start", f"Not now: {ctx.records(task)}/spec.md changed after ./wf spec submit. "
                                              "Tell the manager: change, to submit the spec again.")
    plan_text = specplan.render_plan(tid, task["goal"], steps_of(task))
    if (folder / "plan.md").read_text(encoding="utf-8") != plan_text:
        raise ctx.refuse(task, "build start", f"Not now: {ctx.records(task)}/plan.md changed after ./wf plan submit. "
                                              "Tell the manager: change, to plan again.")
    adopted = None
    if ctx.git.branch_exists(branch):
        commits = ctx.git.commits(base, branch)
        if len(commits) == 1 and commits[0].subject == records_subject(task):
            adopted = commits[0].sha  # a build start that committed and then failed to record it
        elif commits:
            raise ctx.refuse(task, "build start", f"Not now: the branch {branch} already exists with other commits. "
                                                  "A person renames it, or tell the manager: cancel.")
        ctx.git.switch(branch)
    else:
        ctx.git.switch(branch, create_from=base)
    if adopted is None:
        env = {"GIT_LITERAL_PATHSPECS": "1"}
        ctx.git.run("add", "-f", "--", *names, env=env)
        ctx.git.run("commit", "-q", "-m", records_subject(task), "--", *names, env=env)
    sha = ctx.git.head()
    with ctx.store.transaction() as st:
        task = ctx.check(st, "build start")
        if not task["build"]["started"]:
            task["build"]["started"] = True
            task["build"]["started_at"] = now()
            task["build"]["records_commit"] = sha
    ctx.log(task, "build start")
    steps = steps_of(task)
    first = steps[0]
    hint = (f"step 1 is manual: do it, then ./wf commit --step 1 -m \"<what>\" -- <files>" if not first.runs
            else "write its test, then ./wf run --step 1 --red")
    listed = ", ".join(str(step.k) for step in steps)
    return (f'{tid} build started on {branch}: first commit {short_sha(sha)} "{records_subject(task)}".\n'
            f"Steps to build: {listed}. Start with step 1: {hint}")


# ---------------------------------------------------------------- run


def _run_suite(ctx: Ctx, task: dict, head: str, command_name: str) -> tuple[dict, list[str]]:
    """Run every suite command at HEAD. Returns the suite record and the lines to print; refuses on a failure."""
    if not ctx.config.suite:
        raise ctx.refuse(task, command_name, 'There is no suite in .workflow/project.toml: a person adds one (suite = ["npm test"]).')
    clean_before = not ctx.clean_entries(task)
    parts: list[str] = []
    lines: list[str] = []
    failed: evidence.RunResult | None = None
    for command in ctx.config.suite:
        result = evidence.execute(command, ctx.project, ctx.config.run_timeout)
        parts.append(evidence.header(result, ctx.project, head) + result.output + "\n")
        if result.timed_out or result.exit != 0:
            failed = result
            break
        found = evidence.digest(result.output, limit=2, decisive_only=True)
        lines.append(f"{short(command, 60)}: exit 0" + (f" ({short(found[0], 90)})" if found else ""))
    clean = clean_before and not ctx.clean_entries(task)
    folder = evidence_dir(ctx, task)
    name = f"suite-{head[:7]}"
    if failed is not None:
        path = evidence.write(evidence.attempt_path(folder, name), "\n".join(parts))
        why = f"did not finish in {ctx.config.run_timeout:.0f}s" if failed.timed_out else f"exit {failed.exit}"
        shown = evidence.digest(failed.output, failed=True)
        raise ctx.refuse(task, command_name, "\n".join([
            f"Suite FAIL at {head[:7]}: {short(failed.command, 60)} {why}. Saved as {ctx.relative(path)}.", *shown,
            "Fix the code (never the test), then run ./wf run --suite again."]))
    path = evidence.write(folder / f"{name}.txt", "\n".join(parts))
    record = {"sha": head, "clean": clean, "file": path.relative_to(ctx.task_dir(task["id"])).as_posix(), "at": now()}
    return record, lines


def cmd_run(ctx: Ctx, args) -> str:
    state = ctx.load()
    task = ctx.check(state, "run")
    if args.suite:
        if args.red or args.green or args.cmd:
            raise ctx.refuse(task, "run", "./wf run --suite takes no --red, --green or command: the suite is in .workflow/project.toml.")
    elif not (args.red or args.green):
        raise ctx.refuse(task, "run", "Say --red or --green: --red when the test must fail, --green when it must pass.")
    label = "Suite"
    if args.step is not None:
        step = _step(task, args.step) if any(s.k == args.step for s in steps_of(task)) else None
        if step is None:
            raise ctx.refuse(task, "run", f"Not now: there is no step {args.step}: the plan has steps 1 to {len(steps_of(task))}.")
        if args.cmd:
            raise ctx.refuse(task, "run", f"Step {step.k}'s command is in the plan: {short(step.command, 80)}. Run ./wf run --step {step.k} --red or --green.")
        if not step.runs:
            raise ctx.refuse(task, "run", f"Not now: step {step.k} is manual: there is nothing to run. Do it, then ./wf commit --step {step.k} -m \"<what>\" -- <files>")
        command, label, key, number = step.command, f"Step {step.k}", "evidence", step.k
    elif args.fix is not None:
        if not task["fix_round"]:
            raise ctx.refuse(task, "run", "Not now: there is no fix round: run a step with --step.")
        if args.fix != task["fix_round"]:
            raise ctx.refuse(task, "run", f"Not now: this is fix round {task['fix_round']}: use --fix {task['fix_round']}.")
        if not args.cmd:
            raise ctx.refuse(task, "run", f'Say the command that runs the fix test: ./wf run --fix {args.fix} --red "<command>"')
        command, label, key, number = args.cmd, f"Fix {args.fix}", "fixes", args.fix
    ctx.ensure_on_branch(task, "run")
    head = ctx.git.head()
    if args.suite:
        record, lines = _run_suite(ctx, task, head, "run")
        try:
            with ctx.store.transaction() as st:
                task = ctx.check(st, "run")
                task["build"]["suite"] = record
        except Refused as exc:
            raise Refused(f"{exc}\nThe output is saved in {record['file']} but not recorded: run it again when the task goes on.") from None
        ctx.log(task, "run --suite")
        if record["clean"]:
            first = (f"Suite green at {head[:7]} (clean tree): {count(len(ctx.config.suite), 'command')}, "
                     f"saved {ctx.relative(ctx.task_dir(task['id']) / record['file'])}")
            last = "Next: the next step, or ./wf build done"
        else:
            first = f"Suite green at {head[:7]}, but the tree was not clean: build done needs a green suite on a clean tree"
            last = "Next: commit the changes with ./wf commit, then ./wf run --suite again"
        return "\n".join([first, *lines, last])
    expect = "red" if args.red else "green"
    result = evidence.execute(command, ctx.project, ctx.config.run_timeout)
    good = not result.timed_out and ((result.exit != 0) if expect == "red" else (result.exit == 0))
    folder = evidence_dir(ctx, task)
    name = f"{'step' if key == 'evidence' else 'fix'}-{number}-{expect}"
    path = evidence.write(folder / f"{name}.txt" if good else evidence.attempt_path(folder, name),
                          evidence.header(result, ctx.project, head) + result.output)
    shown = evidence.digest(result.output, failed=result.exit != 0)
    if not good:
        if result.timed_out:
            why = f"it did not finish in {ctx.config.run_timeout:.0f}s and was stopped"
        elif expect == "red":
            why = "the command passed (exit 0), so the test does not fail yet"
        else:
            why = f"still failing (exit {result.exit})"
        advice = ("Make the test fail because the behaviour is missing, then run it again." if expect == "red"
                  else "Fix the code (never the test), then run it again.")
        raise ctx.refuse(task, "run", "\n".join([f"{label} {expect}: {why}. Saved as {ctx.relative(path)}.", *shown, advice]))
    entry = _evidence_entry(ctx, task, path, result, head)
    try:
        with ctx.store.transaction() as st:
            task = ctx.check(st, "run")
            task["build"][key].setdefault(str(number), {})[expect] = entry
    except Refused as exc:
        raise Refused(f"{exc}\nThe output is saved in {ctx.relative(path)} but not recorded: run it again when the task goes on.") from None
    ctx.log(task, f"run --{'step' if key == 'evidence' else 'fix'} {number} --{expect}")
    flag = f"--step {number}" if key == "evidence" else f"--fix {number}"
    if expect == "red":
        hint = f'Next: ./wf commit {flag} --test -m "<what it checks>" -- <test files>'
    else:
        hint = f'Next: ./wf commit {flag} -m "<what it adds>" -- <files>, then ./wf run --suite'
    return "\n".join([f"{label} {expect.upper()}: exit {result.exit} at {head[:7]} (saved {ctx.relative(path)})", *shown, hint])


# ---------------------------------------------------------------- commit


def _paths(ctx: Ctx, task: dict, files: list[str]) -> list[str]:
    root = ctx.project.resolve()
    out: list[str] = []
    for raw in files:
        if not raw.strip():
            raise ctx.refuse(task, "commit", "An empty file name: name each file to commit.")
        path = Path(raw).expanduser()
        full = (path if path.is_absolute() else root / path).resolve(strict=False)
        try:
            rel = full.relative_to(root)
        except ValueError:
            raise ctx.refuse(task, "commit", f"{raw} is outside the project folder: only files in it can be committed.") from None
        name = rel.as_posix()
        parts = rel.parts
        if not parts:
            raise ctx.refuse(task, "commit", "Name the files to commit, not the whole folder.")
        if "node_modules" in parts:
            raise ctx.refuse(task, "commit", f"{raw}: node_modules never goes into git.")
        if parts[0] in BUILD_DIRS:
            raise ctx.refuse(task, "commit", f"{raw}: build output never goes into git.")
        if parts[0] in (".git", ".workflow") or name == "wf":
            raise ctx.refuse(task, "commit", f"{raw} is wf's own or git's own file: it never goes into git.")
        if full.is_dir():
            raise ctx.refuse(task, "commit", f"{raw} is a folder: name the files in it.")
        if not full.exists() and not ctx.git.tracked(name):
            raise ctx.refuse(task, "commit", f"{raw} does not exist.")
        if name not in out:
            out.append(name)
    return out


def cmd_commit(ctx: Ctx, args) -> str:
    state = ctx.load()
    task = ctx.check(state, "commit")
    if "\n" in args.message or "\r" in args.message:
        raise ctx.refuse(task, "commit", "Write the message on one line: what it checks or adds.")
    message = " ".join(args.message.split())
    if not message:
        raise ctx.refuse(task, "commit", 'Say what it checks or adds: -m "<what>".')
    if len(message) > MAX_MESSAGE:
        raise ctx.refuse(task, "commit", f"Keep the message under {MAX_MESSAGE} characters.")
    files = _paths(ctx, task, args.files)
    commits = ctx.git.commits(task["base"], task["branch"]) if ctx.git.branch_exists(task["branch"]) else []
    marks = commit_marks(commits)
    if args.step is not None:
        if not any(s.k == args.step for s in steps_of(task)):
            raise ctx.refuse(task, "commit", f"Not now: there is no step {args.step}: the plan has steps 1 to {len(steps_of(task))}.")
        step = _step(task, args.step)
        k = step.k
        found = task["build"]["evidence"].get(str(k), {})
        if args.test:
            if not step.runs:
                raise ctx.refuse(task, "commit", f"Not now: step {k} is manual: it has no test commit. Use ./wf commit --step {k} -m \"<what>\" -- <files>")
            if "red" not in found:
                raise ctx.refuse(task, "commit", f"Not now: no red run for step {k} yet. Run ./wf run --step {k} --red first.")
            subject = f"step {k} test: {message}"
            hint = f"Next: write the least code, then ./wf run --step {k} --green"
        else:
            if step.runs and k not in marks["step test"]:
                raise ctx.refuse(task, "commit", f"Not now: no test commit for step {k} yet. Commit the test first: ./wf commit --step {k} --test ...")
            if step.runs and "green" not in found:
                raise ctx.refuse(task, "commit", f"Not now: no green run for step {k} yet. Run ./wf run --step {k} --green first.")
            subject = f"step {k}: {message}"
            hint = "Next: ./wf run --suite, then the next step or ./wf build done"
    else:
        r = args.fix
        if not task["fix_round"]:
            raise ctx.refuse(task, "commit", "Not now: there is no fix round: commit a step with --step.")
        if r != task["fix_round"]:
            raise ctx.refuse(task, "commit", f"Not now: this is fix round {task['fix_round']}: use --fix {task['fix_round']}.")
        if args.test:
            subject = f"fix {r} test: {message}"
            hint = f'Next: fix the code, then ./wf run --fix {r} --green "<command>"'
        else:
            if r not in marks["fix test"]:
                raise ctx.refuse(task, "commit", f"Not now: no test commit for fix {r} yet. Commit the test first: ./wf commit --fix {r} --test ...")
            subject = f"fix {r}: {message}"
            hint = "Next: ./wf run --suite, then ./wf build done"
    ctx.ensure_on_branch(task, "commit")
    env = {"GIT_LITERAL_PATHSPECS": "1"}
    try:
        ctx.git.run("add", "--", *files, env=env)
        ctx.git.run("commit", "-q", "-m", subject, "--", *files, env=env)
    except GitError as exc:
        text = str(exc)
        if "nothing" in text and "commit" in text or "no changes added" in text:
            raise ctx.refuse(task, "commit", f"Not now: nothing changed in {', '.join(files)}.") from None
        raise ctx.refuse(task, "commit", f"git refused the commit: {text}") from None
    sha = ctx.git.head()
    ctx.log(task, "commit", detail=subject)
    word = "file" if len(files) == 1 else "files"
    return f"{subject} -> {sha[:7]} ({len(files)} {word})\n{hint}"


# ---------------------------------------------------------------- build done


def cmd_build_done(ctx: Ctx, args) -> str:
    state = ctx.load()
    current = ctx.active(state)
    moved = bool(current and current["head"]) and ctx.branch_tip(current) != current["head"]
    task = ctx.check(state, "build done", head_moved=moved)
    from_stage, old_head = task["stage"], task["head"]
    ctx.ensure_on_branch(task, "build done")
    problems = []
    entries = ctx.clean_entries(task)
    if entries:
        problems.append(f"the tree is not clean: {', '.join(entries[:4])}: commit with ./wf commit")
    commits = ctx.git.commits(task["base"], "HEAD")
    problems += commit_shape_problems(task, commits)
    problems += evidence_problems(ctx, task)
    if problems:
        shown = problems[:6] + ([f"... and {len(problems) - 6} more"] if len(problems) > 6 else [])
        raise ctx.refuse(task, "build done", "\n".join(["Build not done:", *shown, "Fix it, then run ./wf build done again."]))
    head = ctx.git.head()
    suite = task["build"]["suite"]
    ran = ""
    new_suite = None
    if not (suite and suite["sha"] == head and suite["clean"]):
        new_suite, _ = _run_suite(ctx, task, head, "build done")
        ran = " (wf ran the suite now)"
    with ctx.store.transaction() as st:
        current = ctx.active(st)
        moved = bool(current and current["head"]) and ctx.branch_tip(current) != current["head"]
        task = ctx.check(st, "build done", head_moved=moved)
        if new_suite is not None:
            task["build"]["suite"] = new_suite
        task["head"] = head
        task["review"] = None
        task["tests"] = empty_tests()
        task["fix_reason"] = None
        task["stage"] = "review"
    if from_stage != "build":
        ctx.slip(task, "build done", f"HEAD moved after build done at {from_stage} (was {short_sha(old_head)}, now {short_sha(head)})")
    ctx.log(task, "build done")
    total = len(steps_of(task))
    word = "step" if total == 1 else "steps"
    return (f"{task['id']} built: {total} of {total} {word}, {len(commits)} commits on {task['branch']}, "
            f"suite green at {head[:7]}{ran}.\nNext: review")


# ---------------------------------------------------------------- the builder's turn (status --for builder)


def builder_turn(ctx: Ctx, task: dict) -> str:
    tid = task["id"]
    if not task["build"]["started"]:
        return (f"Your turn: start the build of {tid}: ./wf build start\n"
                "Then per step: write the test, ./wf run --step <k> --red, commit the test, write the least code, "
                "--green, commit the code, ./wf run --suite.")
    tip = ctx.branch_tip(task) or task["base"]
    commits = ctx.git.commits(task["base"], tip)
    marks = commit_marks(commits)
    steps = steps_of(task)
    done = [s.k for s in steps if s.k in marks["step"]]
    lines = [f"Your turn: build {tid} on {task['branch']} at {short_sha(tip)}.",
             f"Steps with a code commit: {len(done)} of {len(steps)}."]
    r = task["fix_round"]
    if r and task["fix_reason"]:
        reason = task["fix_reason"]
        lines.append(f"Fix {r}: {reason['label']}.")
        details = reason.get("details") or [reason["summary"]]
        lines += bounded([short(d, 170) for d in details], 600, "./wf log")
        if r not in marks["fix test"]:
            lines.append(f'Next for fix {r}: write a test that shows the problem, ./wf run --fix {r} --red "<command>", '
                         f"then ./wf commit --fix {r} --test ...")
        elif r not in marks["fix"]:
            lines.append(f'Next for fix {r}: fix the code, ./wf run --fix {r} --green "<command>", then ./wf commit --fix {r} ...')
        else:
            lines.append("Next: ./wf run --suite, then ./wf build done")
    else:
        for step in steps:
            if step.k in marks["step"]:
                continue
            k = step.k
            found = task["build"]["evidence"].get(str(k), {})
            if not step.runs:
                hint = f'step {k} is manual: do it, then ./wf commit --step {k} -m "<what>" -- <files>'
            elif "red" not in found:
                hint = f"write its test, then ./wf run --step {k} --red"
            elif k not in marks["step test"]:
                hint = f'commit the test: ./wf commit --step {k} --test -m "<what it checks>" -- <test files>'
            elif "green" not in found:
                hint = f"write the least code, then ./wf run --step {k} --green"
            else:
                hint = f'commit the code: ./wf commit --step {k} -m "<what it adds>" -- <files>'
            lines.append(f"Next for step {k}: {hint}")
            break
        else:
            lines.append("Every step has its commits. Next: ./wf build done (it runs the suite if it is not green at HEAD)")
    both = [str(s.k) for s in steps if s.runs and {"red", "green"} <= set(task["build"]["evidence"].get(str(s.k), {}))]
    if both:
        lines.append(f"Evidence: red and green for {numbered('step', both)}.")
    suite = task["build"]["suite"]
    if suite and suite["sha"] == tip and suite["clean"]:
        lines.append(f"Suite: green at {short_sha(tip)} on a clean tree.")
    else:
        lines.append("Suite: not green at the last commit yet (./wf run --suite after the commit).")
    entries = ctx.clean_entries(task)
    if entries:
        lines.append(f"Uncommitted: {', '.join(entries[:5])}" + (f" and {len(entries) - 5} more." if len(entries) > 5 else "."))
    return "\n".join(lines)
