"""The hand-off packet (spec section 9): four lines, the next agent's alias first, a pure function of the state.

`./wf next` and `./wf resume` print it; the agent posts it with post_message as plain text, so the
mention wakes the next agent. Under 600 characters whatever the goal, the quotes or the findings say.
"""

from __future__ import annotations

from .chat import short, wrapped
from .config import Config
from .machine import short_sha


LINE = 178  # a packet line stays under the 180-character house limit


def waiting_line(task: dict) -> str:
    waiting = task["waiting"]
    if waiting["kind"] == "loops":
        return wrapped(f"WAITING ON YOU: {task['id']} has looped {task['loops']} times (last: {short(waiting['question'], 160)}). "
                     "Reply continue (one more round) or cancel. Default: continue. Tell the manager.")
    question = short(waiting["question"], 300)
    if question[-1:] not in ".?!":
        question += "."
    default = short(waiting["default"], 120).rstrip(".")
    return wrapped(f"WAITING ON YOU: {question} Default: {default}. Tell the manager ok, or answer in your words.")


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _with_words(base: str, task: dict) -> str:
    """BASE plus the person's latest words for this stage (a change or an answer), cut to fit one line."""
    stage = task["stage"]
    quote = None
    for candidate in reversed(task["quotes"]):
        if candidate.get("stage") != stage:
            continue
        if (candidate["kind"] == "change" and stage == "spec") or (candidate["kind"] == "answer" and candidate.get("for") == "ask"):
            quote = candidate
            break
    if quote is None:
        return base
    who = short(quote["by"], 30)
    label = f'; change asked by {who}: "' if quote["kind"] == "change" else f'; answer from {who}: "'
    room = LINE - len(base) - len(label) - 1
    if room < 15:
        return base
    return f'{base}{label}{short(quote["text"], room)}"'


def packet(task: dict, config: Config) -> str:
    """The packet for TASK's current stage, or a plain line when there is nobody to hand to."""
    a = config.alias
    tid, branch, head = task["id"], task["branch"], short_sha(task["head"])
    folder = f"docs/tasks/{tid}"
    stage = task["stage"]
    if stage == "cancelled":
        return f"Nothing to hand off: {tid} was cancelled."
    if stage == "done":
        ship = task["ship"] or {}
        where = ship.get("url") or (f"merged into {config.main} as {short_sha(ship.get('merge_sha'))}" if ship.get("merge_sha") else "pushed")
        return "\n".join([
            f"@{a('manager')} {tid} shipped at {short_sha(ship.get('sha'))}: {short(where, 120)}",
            f"See: ./wf verify {tid} (the proof chain); .workflow/tasks/{tid}/test-report.md",
            "Do: ./wf note, post it, then wait for a person",
            f"Prove: ./wf verify {tid} prints shipped at {short_sha(ship.get('sha'))}",
        ])
    if task["waiting"]:
        return waiting_line(task)
    if task["stopped"]:
        return f"{tid} is stopped. Tell the manager resume to go on."
    items = len(task["spec"]["items"]) if task["spec"] else 0
    if stage == "spec":
        lines = [
            f'@{a("specifier")} {tid}: write the spec for "{short(task["goal"], 100)}"',
            _with_words(f"See: {folder}/goal.md (the person's words); the code the goal touches", task),
            "Do: ./wf skill specifier, then pipe the items and the Assumed line to ./wf spec submit",
            "Prove: ./wf spec submit checks each item has a check type and that there is an Assumed line",
        ]
    elif stage == "plan":
        lines = [
            f"@{a('planner')} {tid}: plan the {_count(items, 'spec item')}",
            _with_words(f"See: {folder}/spec.md ({_count(items, 'item')}); {folder}/goal.md", task),
            "Do: ./wf skill planner, then pipe the steps to ./wf plan submit",
            "Prove: ./wf plan submit refuses a plan that leaves an item without a step",
        ]
    elif stage == "build" and task["fix_round"] and task["fix_reason"]:
        r = task["fix_round"]
        reason = task["fix_reason"]
        lines = [
            f"@{a('builder')} {tid}: fix {r} on {branch} ({reason['label']})",
            _with_words(f"See: {short(reason['summary'], 100)}; ./wf status --for {a('builder')} shows all of it", task),
            f"Do: ./wf skill builder; a fix {r} test commit, then a fix {r} commit; ./wf run --suite; ./wf build done",
            "Prove: ./wf build done checks the fix commits and the suite at HEAD",
        ]
    elif stage == "build":
        steps = len(task["plan"]["steps"]) if task["plan"] else 0
        span = "step 1" if steps == 1 else f"steps 1-{steps}"
        lines = [
            f"@{a('builder')} {tid}: build {span} on {branch}",
            _with_words(f"See: {folder}/plan.md (steps, test files, commands); {folder}/spec.md ({_count(items, 'item')})", task),
            "Do: ./wf skill builder, then ./wf build start; per step red, green, two commits; then ./wf build done",
            "Prove: ./wf run saves the output; ./wf build done checks the commits",
        ]
    elif stage == "review":
        lines = [
            f"@{a('reviewer')} {tid}: review {branch} at {head}",
            _with_words(f"See: {folder}/spec.md ({_count(items, 'item')}); git diff {short_sha(task['base'])}..{head}, tests first", task),
            "Do: ./wf skill reviewer, then ./wf review approve or ./wf review changes with the findings",
            f"Prove: wf records the verdict at {head} and refuses if HEAD moved",
        ]
    elif stage == "test":
        lines = [
            f"@{a('tester')} {tid}: test {branch} at {head} ({_count(items, 'item')})",
            _with_words(f"See: {folder}/spec.md (the items and their checks)", task),
            "Do: ./wf skill tester, then ./wf test start; ./wf test item per item; ./wf test done",
            "Prove: every item gets one line of evidence you produced in the clean clone",
        ]
    else:
        lines = [
            f"@{a('shipper')} {tid}: ship {branch} at {head}",
            _with_words(f"See: ./wf status --for {a('shipper')} (it runs the gate and prints the ready line)", task),
            "Do: ./wf skill shipper, then post the ready line and run ./wf ship (a person clicks Allow once)",
            "Prove: ./wf ship runs the gate again and records the pull request or the merge",
        ]
    return "\n".join(lines)
