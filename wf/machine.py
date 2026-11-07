"""The transition table of spec section 6.2, as data and one function.

`refusal(task, command)` is None when the command may run now, else the refusal text. It looks only at
the state; the git facts (HEAD moved, clean tree, on the branch) are checked by the commands that need them.
"""

from __future__ import annotations

from collections.abc import Callable

from .config import STAGE_OWNER

STAGES = ("spec", "plan", "build", "review", "test", "ship", "done", "cancelled")
FINAL = frozenset({"done", "cancelled"})
WORK_STAGES = ("spec", "plan", "build", "review", "test", "ship")

# The stage commands and the stages they run in. Everything else is refused (exit 2, state unchanged).
STAGE_COMMANDS: dict[str, frozenset[str]] = {
    "spec submit": frozenset({"spec"}),
    "change": frozenset({"spec", "plan", "build"}),
    "plan submit": frozenset({"plan"}),
    "build start": frozenset({"build"}),
    "run": frozenset({"build"}),
    "commit": frozenset({"build"}),
    "build done": frozenset({"build", "review", "test", "ship"}),
    "review": frozenset({"review"}),
    "test start": frozenset({"test"}),
    "test item": frozenset({"test"}),
    "test done": frozenset({"test"}),
    "ship": frozenset({"ship"}),
    "ask": frozenset(WORK_STAGES),
}
# A person's commands, through the manager. They work while a question waits or the task is stopped:
# a person can always answer, pause, go on or end a task (Claude's call, plan Task 1).
PERSON_COMMANDS = frozenset({"answer", "cancel", "stop", "resume"})
WRITE_COMMANDS = frozenset(STAGE_COMMANDS) | PERSON_COMMANDS | {"new"}

NO_TASK = "Not now: no active task. Next: a person tells the manager what to build"


def is_active(task: dict | None) -> bool:
    return task is not None and task["stage"] not in FINAL


def owner(task: dict | None) -> str:
    """The role whose turn it is."""
    if not is_active(task) or task["waiting"] or task["stopped"]:
        return "manager"
    return STAGE_OWNER[task["stage"]]


def short_sha(sha: str | None) -> str:
    return sha[:7] if sha else "?"


def next_line(task: dict | None, alias: Callable[[str], str] = lambda role: role) -> str:
    """`Next: <who> <does what>`, from the state alone."""
    if task is None or task["stage"] == "cancelled":
        return "Next: a person tells the manager what to build"
    if task["stage"] == "done":
        return f"Next: {alias('manager')} posts the closing note"
    if task["stopped"]:
        return "Next: a person tells the manager resume"
    if task["waiting"]:
        return "Next: a person answers the question (tell the manager)"
    stage, branch, head = task["stage"], task["branch"], short_sha(task["head"])
    if stage == "spec":
        return f"Next: {alias('specifier')} writes the spec"
    if stage == "plan":
        return f"Next: {alias('planner')} writes the plan"
    if stage == "build":
        if not task["build"]["started"]:
            return f"Next: {alias('builder')} starts the build on {branch}"
        if task["fix_round"] and task["fix_reason"]:
            return f"Next: {alias('builder')} makes fix {task['fix_round']} on {branch}"
        return f"Next: {alias('builder')} builds the steps on {branch}"
    if stage == "review":
        return f"Next: {alias('reviewer')} reviews {branch} at {head}"
    if stage == "test":
        return f"Next: {alias('tester')} tests {branch} at {head}"
    return f"Next: {alias('shipper')} ships {branch} at {head} (a person clicks Allow once)"


def not_now(task: dict, alias: Callable[[str], str] = lambda role: role) -> str:
    return f"Not now: {task['id']} is at {task['stage']}. {next_line(task, alias)}"


def refusal(task: dict | None, command: str, *, head_moved: bool = False,
            alias: Callable[[str], str] = lambda role: role) -> str | None:
    """None when COMMAND may run on TASK now; else the text of the refusal."""
    if command == "new":
        if is_active(task):
            return f"Not now: {task['id']} is active (at {task['stage']}): cancel it or wait. {next_line(task, alias)}"
        return None
    if not is_active(task):
        return NO_TASK
    tid = task["id"]
    if command in PERSON_COMMANDS:
        if command == "answer" and not task["waiting"]:
            return f"Not now: no question is waiting on {tid}. {next_line(task, alias)}"
        return None
    if command not in STAGE_COMMANDS:
        raise ValueError(f"unknown command {command!r}")
    if task["stopped"]:
        return f"Not now: {tid} is stopped. {next_line(task, alias)}"
    if task["waiting"]:
        question = task["waiting"]["question"]
        return f"Not now: {tid} is waiting on a person: {question} {next_line(task, alias)}"
    stage = task["stage"]
    if stage not in STAGE_COMMANDS[command]:
        return not_now(task, alias)
    started = task["build"]["started"]
    if command == "change" and stage == "build" and started:
        return f"Not now: {tid} is being built: cancel it and start a new task, or let it finish. {next_line(task, alias)}"
    if command in ("run", "commit") and not started:
        return f"Not now: the build of {tid} has not started. Next: {alias('builder')} runs ./wf build start"
    if command == "build done":
        if stage == "build" and not started:
            return f"Not now: the build of {tid} has not started. Next: {alias('builder')} runs ./wf build start"
        if stage != "build" and not head_moved:
            return not_now(task, alias)
    if command in ("test item", "test done") and not task["tests"]["clone"]:
        return f"Not now: {tid} has no clean clone yet. Next: {alias('tester')} runs ./wf test start"
    if command == "test done":
        numbers = [item["n"] for item in task["spec"]["items"]]
        missing = [n for n in numbers if str(n) not in task["tests"]["items"]]
        if missing:
            listed = ", ".join(map(str, missing))
            return f"Not now: items {listed} of {tid} have no result yet. Next: {alias('tester')} reports them with ./wf test item"
    return None
