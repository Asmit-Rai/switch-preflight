"""The commands that start a task, move it through spec and plan, and talk to people (plan Task 1)."""

from __future__ import annotations

import re
from pathlib import Path

from . import machine, packets, specplan
from .chat import bounded, short, wrapped
from .config import ROLES
from .context import Ctx
from .errors import Refused, WfError
from .machine import next_line, owner, short_sha
from .state import new_task
from .timeutil import hhmm, minutes, now

SKILLS = Path(__file__).resolve().parent / "skills"
_STOP_WORDS = frozenset(
    "a an the to of for and or in on with add make please so that it is be we i my our this new".split()
)
LATE_PICKUP_MIN = 10


# ---------------------------------------------------------------- helpers


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def slug(goal: str) -> str:
    """Branch words from a goal: lowercase, dashes, at most 24 characters, filler words dropped."""
    words = re.findall(r"[a-z0-9]+", goal.lower())
    useful = [w for w in words if w not in _STOP_WORDS] or words
    out = ""
    for word in useful:
        candidate = f"{out}-{word}" if out else word
        if len(candidate) > 24:
            break
        out = candidate
    if not out and useful:
        out = useful[0][:24]
    return out or "task"


def _said(text: str, what: str, limit: int = 2000) -> str:
    one = " ".join(str(text).split())
    if not one:
        raise Refused(f"Say {what}: it is empty.")
    if len(one) > limit:
        raise Refused(f"{what[0].upper() + what[1:]} is over {limit} characters: say it shorter.")
    return one


def _name(by: str) -> str:
    return _said(by, "who said it (--by <name>)", 80)


def next_number(ctx: Ctx, state: dict) -> int:
    """One more than any task number already used: in the state, in branch names, in docs/tasks."""
    used = [task["n"] for task in state["tasks"].values()]
    for ref in ctx.git.refs():
        match = re.match(r"^(?:[^/]+/)?t(\d+)-", ref)
        if match:
            used.append(int(match.group(1)))
    tasks_dir = ctx.project / "docs" / "tasks"
    if tasks_dir.is_dir():
        for child in tasks_dir.iterdir():
            match = re.fullmatch(r"T(\d+)", child.name)
            if match:
                used.append(int(match.group(1)))
    return max(used, default=0) + 1


def _record_handoff(ctx: Ctx, task_id: str, role: str) -> None:
    """Bookkeeping of `next` and `resume`: when the packet was printed and how often (never blocks)."""
    try:
        with ctx.store.transaction(timeout=3) as state:
            task = state["tasks"].get(task_id)
            if task is None:
                return
            key = f"{role}:{task['stage']}:{task['fix_round']}"
            handoff = task.get("handoff")
            if handoff and handoff.get("key") == key:
                handoff["posted_count"] += 1
                handoff["printed_at"] = now()
            else:
                task["handoff"] = {"key": key, "to": role, "printed_at": now(), "posted_count": 1, "received_at": None}
            count = task["handoff"]["posted_count"]
            ctx.log(task, "next", f"posted to {ctx.alias(role)}", key=key, post=count)
    except WfError:
        pass


def _packet(ctx: Ctx, task: dict) -> str:
    text = packets.packet(task, ctx.config)
    if text.startswith("@"):
        role = "manager" if task["stage"] == "done" else machine.owner(task)
        _record_handoff(ctx, task["id"], role)
    return text


def _handoff_line(ctx: Ctx, task: dict) -> str | None:
    turn = owner(task)
    if turn == "manager" or not machine.is_active(task):
        return None
    handoff = task.get("handoff")
    key = f"{turn}:{task['stage']}:{task['fix_round']}"
    if not handoff or handoff.get("key") != key:
        return f"The hand-off to {ctx.alias(turn)} is not posted yet (./wf next posts it)."
    if handoff["received_at"] is None:
        waited = minutes(handoff["printed_at"], now())
        times = f" Posted {handoff['posted_count']} times." if handoff["posted_count"] > 1 else ""
        return f"Hand-off to {ctx.alias(turn)} posted {hhmm(handoff['printed_at'])}, not picked up ({waited} min).{times}"
    line = f"{ctx.alias(turn)} picked up the hand-off at {hhmm(handoff['received_at'])}."
    activity = task.get("last_activity")
    if activity and activity["at"] != handoff["received_at"]:
        line += f" Last: {short(activity['command'], 60)} at {hhmm(activity['at'])}."
    return line


def _stage_detail(ctx: Ctx, task: dict) -> str:
    folder = ctx.records(task)
    stage = task["stage"]
    items = len(task["spec"]["items"]) if task["spec"] else 0
    if stage == "spec":
        return f"Goal in {folder}/goal.md. Branch {task['branch']} comes at the build."
    if stage == "plan":
        return f"Spec: {items} items ({folder}/spec.md)."
    if stage == "build" and not task["build"]["started"]:
        return f"Plan: {len(task['plan']['steps'])} steps ({folder}/plan.md). Branch {task['branch']} not made yet."
    if stage == "build":
        return f"Building on {task['branch']}; fix round {task['fix_round']}." if task["fix_round"] else f"Building on {task['branch']}."
    if stage == "test":
        reported = len(task["tests"]["items"])
        return f"Branch {task['branch']} at {short_sha(task['head'])}. Items reported: {reported} of {items}."
    return f"Branch {task['branch']} at {short_sha(task['head'])}."


def stage_lines(ctx: Ctx, task: dict) -> list[str]:
    """Where an active task is, for a person: header, detail, hand-off, then a waiting, stopped or Next line."""
    lines = [f"{task['id']} at {task['stage']}: {short(task['goal'], 100)}", _stage_detail(ctx, task)]
    handoff = _handoff_line(ctx, task)
    if handoff:
        lines.append(handoff)
    if task["waiting"]:
        lines.append(packets.waiting_line(task))
        return lines
    if task["stopped"]:
        lines.append(f"Stopped by {task['stopped_by'] or 'a person'} at {hhmm(task['stopped_at'])}. Tell the manager resume to go on.")
    lines.append(next_line(task, ctx.alias))
    return lines


def _last_words(task: dict) -> str:
    ship = task.get("ship") or {}
    if task["stage"] == "done":
        where = ship.get("url") or (f"merged as {short_sha(ship.get('merge_sha'))}" if ship.get("merge_sha") else "pushed")
        return f"{task['id']} shipped at {short_sha(ship.get('sha'))} ({short(where, 100)})"
    return f"{task['id']} was cancelled"


# ---------------------------------------------------------------- new


def cmd_new(ctx: Ctx, args) -> str:
    # "@manager add X" -> "add X": a mention in the goal would ride along in every packet
    goal_text = re.sub(r"^(?:@[\w.-]+[\s,:]+)+", "", str(args.goal).strip())
    goal = _said(goal_text, "the goal")
    by = _name(args.by)
    state = ctx.load()
    ctx.check(state, "new")
    main = ctx.config.main
    if not ctx.git.branch_exists(main):
        raise WfError(f"There is no branch {main}. Set main in .workflow/project.toml to the branch work starts from.")
    ctx.require_clean(None, "new")
    remote = ctx.git.remote_url("origin")
    target = None
    warning = ""
    if remote:
        fetched = ctx.git.run("fetch", "-q", "origin", check=False)
        if fetched.returncode != 0:
            warning = f" ({main} not updated: git fetch failed)"
        target = ctx.git.rev(f"origin/{main}")
        local = ctx.git.rev(main)
        if target and local and not ctx.git.is_ancestor(target, local) and not ctx.git.is_ancestor(local, target):
            raise Refused(f"Not now: {main} and origin/{main} have diverged. A person makes {main} match origin/{main} by hand, then try again.")
    if ctx.git.branch() != main:
        ctx.git.switch(main)
    if target and ctx.git.rev(main) != target and ctx.git.is_ancestor(main, target):
        ctx.git.run("merge", "--ff-only", "-q", f"origin/{main}")
    base = ctx.git.rev(main)
    with ctx.store.transaction() as st:
        ctx.check(st, "new")
        n = next_number(ctx, st)
        task_id = f"T{n}"
        branch = f"t{n}-{slug(goal)}"
        at = now()
        folder = ctx.project / "docs" / "tasks" / task_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "goal.md").write_text(f"# {task_id} goal\n\n{goal_text}\n\nSaid by {by} at {at}.\n", encoding="utf-8")
        task = new_task(n, goal, by, branch, base, at)
        st["tasks"][task_id] = task
        st["active"] = task_id
    ctx.log(task, "new", by=by, quote=short(goal, 300))
    return f"{task_id} started: {short(goal, 100)}. Branch {branch}.{warning}\nNext: spec"


# ---------------------------------------------------------------- status and next


def _turn_text(ctx: Ctx, task: dict | None, role: str, state: dict) -> str:
    a = ctx.alias
    if role == "manager":
        if not machine.is_active(task):
            last = ctx.last(state)
            text = "Your turn: no active task. When a person asks for something, run ./wf new \"<their words>\" --by <their name>."
            if last is not None and last["stage"] == "done":
                text += f"\nLast: {_last_words(last)}. If its closing note is not posted yet: ./wf note, then post it."
            return text
        if task["waiting"]:
            return (f"Your turn: {task['id']} waits on a person.\n{packets.waiting_line(task)}\n"
                    "When the person answers: ./wf answer --by <name> --quote \"<their words>\", then ./wf next and post it.")
        if task["stopped"]:
            return (f"Your turn: {task['id']} is stopped (by {task['stopped_by'] or 'a person'} at {hhmm(task['stopped_at'])}).\n"
                    "When a person says resume: ./wf resume, and post what it prints.")
        # The manager is the door for people: whoever holds the stage, a person may always ask it for
        # status, stop, resume, change, cancel, or ship after a declined card.
        return "\n".join([f"Your turn: you answer people. The agents are working on {task['id']}.",
                           *stage_lines(ctx, task),
                           "Do what the person asked: status, stop, resume, change, cancel, or ship (./wf next reposts the ship hand-off)."])
    folder = ctx.records(task)
    if role == "specifier":
        return (f"Your turn: write the spec for {task['id']}: {short(task['goal'], 100)}\n"
                f"{packets.packet(task, ctx.config).splitlines()[1]}\n"
                "Then: ./wf spec submit, with the items and the Assumed line on stdin.")
    if role == "planner":
        items = len(task["spec"]["items"])
        return (f"Your turn: plan the {plural(items, 'item')} of {task['id']} ({folder}/spec.md).\n"
                "Then: ./wf plan submit, with the steps on stdin.")
    from .cmd_build import builder_turn
    from .cmd_check import reviewer_turn, tester_turn
    from .cmd_ship import shipper_turn

    turns = {"builder": builder_turn, "reviewer": reviewer_turn, "tester": tester_turn, "shipper": shipper_turn}
    return turns[role](ctx, task)


def cmd_status(ctx: Ctx, args) -> str:
    state = ctx.load()
    task = ctx.active(state)
    if args.for_alias is None:
        if task is None:
            last = ctx.last(state)
            if last is None:
                return "No task yet. Tell the manager what you want built."
            return f"No active task. Last: {_last_words(last)}.\nTell the manager what you want built."
        return "\n".join(stage_lines(ctx, task))
    role = ctx.config.role_of(args.for_alias)
    if role is None:
        raise ctx.config.unknown_alias(args.for_alias)
    turn = owner(task)
    if role != turn and role != "manager":
        if task is None:
            return f"Not your turn: no active task. {next_line(None, ctx.alias)}"
        return f"Not your turn: {task['id']} is at {task['stage']}. {next_line(task, ctx.alias)}"
    text = _turn_text(ctx, task, role, state)
    if task is not None and role == turn:
        _record_pickup(ctx, task["id"], role, args.for_alias)
    return text


def _record_pickup(ctx: Ctx, task_id: str, role: str, said: str) -> None:
    try:
        with ctx.store.transaction(timeout=3) as state:
            task = state["tasks"].get(task_id)
            if task is None or owner(task) != role:
                return
            at = now()
            handoff = task.get("handoff")
            key = f"{role}:{task['stage']}:{task['fix_round']}"
            if handoff and handoff.get("key") == key and handoff["received_at"] is None:
                handoff["received_at"] = at
                ctx.log(task, f"status --for {said}", "picked up", key=key)
            task["last_activity"] = {"at": at, "command": f"status --for {said}"}
    except WfError:
        pass


def cmd_next(ctx: Ctx, args) -> str:
    state = ctx.load()
    task = ctx.last(state)
    if task is None:
        return "Nothing to hand off: no task yet."
    return _packet(ctx, task)


def cmd_skill(ctx: Ctx, args) -> str:
    role = ctx.config.role_of(args.alias)
    if role is None:
        raise ctx.config.unknown_alias(args.alias)
    path = SKILLS / f"{role}.md"
    if not path.is_file():
        raise WfError(f"The skill file for {role} is missing from the kit ({path}).")
    return path.read_text(encoding="utf-8").rstrip("\n")


# ---------------------------------------------------------------- spec and plan


def cmd_spec_submit(ctx: Ctx, args) -> str:
    text = ctx.read_input(args.file)
    state = ctx.load()
    task = ctx.check(state, "spec submit")
    try:
        items, assumed = specplan.parse_spec(text)
    except Refused as exc:
        raise ctx.refuse(task, "spec submit", str(exc)) from None
    with ctx.store.transaction() as st:
        task = ctx.check(st, "spec submit")
        body, sha = specplan.render_spec(task["id"], task["goal"], items, assumed)
        path = ctx.project / ctx.records(task) / "spec.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        task["spec"] = {"sha256": sha, "items": [specplan.item_record(i) for i in items], "assumed": assumed}
        task["plan"] = None
        task["stage"] = "plan"
    ctx.log(task, "spec submit")
    head = f"Spec {task['id']} ready: {plural(len(items), 'item')} ({ctx.records(task)}/spec.md)"
    listed = bounded([f"{i.n}. {i.text} [{i.check}]" for i in items], 1000, f"{ctx.records(task)}/spec.md")
    assumed_line = wrapped(f"Assumed: {short(assumed, 160)} Tell the manager if that is wrong.")
    return "\n".join([head, *listed, assumed_line, "Next: plan"])


def cmd_plan_submit(ctx: Ctx, args) -> str:
    text = ctx.read_input(args.file)
    state = ctx.load()
    task = ctx.check(state, "plan submit")
    numbers = [item["n"] for item in task["spec"]["items"]]
    try:
        steps = specplan.parse_plan(text, numbers)
    except Refused as exc:
        raise ctx.refuse(task, "plan submit", str(exc)) from None
    with ctx.store.transaction() as st:
        task = ctx.check(st, "plan submit")
        path = ctx.project / ctx.records(task) / "plan.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(specplan.render_plan(task["id"], task["goal"], steps), encoding="utf-8")
        task["plan"] = {"steps": [specplan.step_record(s) for s in steps]}
        task["stage"] = "build"
    ctx.log(task, "plan submit")
    head = f"Plan {task['id']} ready: {plural(len(steps), 'step')} for {plural(len(numbers), 'item')} ({ctx.records(task)}/plan.md)"
    lines = []
    for step in steps:
        served = ", ".join(map(str, step.items))
        word = "item" if len(step.items) == 1 else "items"
        lines.append(f"{step.k}. {short(step.text, 100)} ({word} {served}, {step.type})")
    return "\n".join([head, *bounded(lines, 1100, f"{ctx.records(task)}/plan.md"), "Next: build"])


# ---------------------------------------------------------------- what people say


def cmd_change(ctx: Ctx, args) -> str:
    quote = _said(args.quote, "what to change")
    by = _name(args.by)
    state = ctx.load()
    ctx.check(state, "change")
    with ctx.store.transaction() as st:
        task = ctx.check(st, "change")
        folder = ctx.project / ctx.records(task)
        for name in ("spec.md", "plan.md"):
            path = folder / name
            if path.is_file() and not ctx.git.tracked(f"{ctx.records(task)}/{name}"):
                ctx.move_aside(path, ctx.task_dir(task["id"]) / "replaced", Path(name).stem)
        task["quotes"].append({"at": now(), "by": by, "kind": "change", "text": quote, "stage": "spec"})
        task["spec"] = None
        task["plan"] = None
        task["stage"] = "spec"
    ctx.log(task, "change", by=by, quote=short(quote, 300))
    return f'{task["id"]} goes back to spec: {by} asked "{short(quote, 200)}".\nNext: spec'


def _cancel(ctx: Ctx, by: str, quote: str, answer: dict | None = None) -> str:
    """End the active task. Untracked records move into .workflow; anything else uncommitted is stashed.

    ANSWER is the person's answer at the loop limit: it is recorded in the same write that cancels, so
    a cancel that fails leaves the question open instead of turning into a silent "continue".
    """
    state = ctx.load()
    task = ctx.check(state, "cancel")
    task_id = task["id"]
    records = ctx.project / ctx.records(task)
    loose_records = records.is_dir() and not ctx.git.tracked(ctx.records(task))
    prefix = ctx.records(task) + "/"
    wf_own = (".workflow/", "wf", ".claude/settings.json.bak")
    leftovers = [p for p in ctx.git.changed_paths()
                 if not (p.startswith(".workflow/") or p in wf_own or (loose_records and p.startswith(prefix)))]
    notes: list[str] = []
    stash = None
    if leftovers:
        # The exact paths, read literally: naming an ignored path (.workflow, the launcher) makes git stash fail.
        ctx.git.run("stash", "push", "--include-untracked", "-m", f"wf {task_id}: uncommitted work at cancel", "--",
                    *leftovers, env={"GIT_LITERAL_PATHSPECS": "1"})
        stash = ctx.git.rev("refs/stash")
        notes.append(f"Uncommitted work is kept in the git stash as {short_sha(stash)}.")
    if loose_records:
        kept = ctx.move_aside(records, ctx.task_dir(task_id), "cancelled-records")
        notes.append(f"The records are kept in {ctx.relative(kept)}.")
    with ctx.store.transaction() as st:
        task = ctx.check(st, "cancel")
        if answer is not None:
            task["quotes"].append(answer)
        task["quotes"].append({"at": now(), "by": by, "kind": "cancel", "text": quote, "stage": task["stage"]})
        task["stage"] = "cancelled"
        task["waiting"] = None
        task["stopped"] = False
        task["cancel_stash"] = stash
        task["ended_at"] = now()
        st["active"] = None
    ctx.log(task, "cancel", by=by, quote=short(quote, 300))
    kept = f" Branch {task['branch']} and the evidence are kept." if task["build"]["started"] else " Nothing was built."
    return "\n".join([f'{task_id} cancelled: {by} said "{short(quote, 200)}".{kept}', *notes, "Nothing needed from you."])


def cmd_cancel(ctx: Ctx, args) -> str:
    return _cancel(ctx, _name(args.by), _said(args.quote, "why it is cancelled"))


def cmd_ask(ctx: Ctx, args) -> str:
    question = _said(args.question, "the question", 300)
    default = _said(args.default, "a default (--default)", 200)
    state = ctx.load()
    ctx.check(state, "ask")
    with ctx.store.transaction() as st:
        task = ctx.check(st, "ask")
        task["waiting"] = {"question": question, "default": default, "stage": task["stage"], "asked_at": now(), "kind": "ask"}
    ctx.log(task, "ask", quote=question)
    return packets.waiting_line(task)


def cmd_answer(ctx: Ctx, args) -> str:
    quote = _said(args.quote, "the answer")
    by = _name(args.by)
    state = ctx.load()
    task = ctx.check(state, "answer")
    waiting = task["waiting"]
    decision = None
    if waiting["kind"] == "loops":
        first = re.sub(r"[^a-z]", "", quote.lower().split()[0]) if quote.split() else ""
        if first in ("continue", "ok", "okay", "yes", "default"):
            decision = "continue"
        elif first == "cancel":
            decision = "cancel"
        else:
            raise ctx.refuse(task, "answer", "At the loop limit the answer is continue or cancel. Ask the person which, then record it.")
    record = {"at": now(), "by": by, "kind": "answer", "text": quote, "stage": waiting["stage"],
              "for": waiting["kind"], "question": waiting["question"], "default": waiting["default"]}
    if decision == "cancel":
        ctx.log(task, "answer", by=by, quote=short(quote, 300))
        return _cancel(ctx, by, quote, answer=record)
    with ctx.store.transaction() as st:
        task = ctx.check(st, "answer")
        task["quotes"].append(record)
        task["waiting"] = None
        if decision == "continue":
            task["loops"] = 0
    ctx.log(task, "answer", by=by, quote=short(quote, 300))
    extra = " The loop count starts again at 0." if decision == "continue" else ""
    return f'Answer recorded for {task["id"]} from {by}: "{short(quote, 200)}".{extra}\n{next_line(task, ctx.alias)}'


def cmd_stop(ctx: Ctx, args) -> str:
    by = _name(args.by) if args.by else "a person"
    state = ctx.load()
    task = ctx.check(state, "stop")
    if task["stopped"]:
        return f"{task['id']} is already stopped (by {task['stopped_by']} at {hhmm(task['stopped_at'])}).\n{next_line(task, ctx.alias)}"
    with ctx.store.transaction() as st:
        task = ctx.check(st, "stop")
        task["stopped"] = True
        task["stopped_by"] = by
        task["stopped_at"] = now()
    ctx.log(task, "stop", by=by)
    return f"{task['id']} stopped at {task['stage']}. Nothing runs until resume.\n{next_line(task, ctx.alias)}"


def cmd_resume(ctx: Ctx, args) -> str:
    state = ctx.load()
    ctx.check(state, "resume")
    with ctx.store.transaction() as st:
        task = ctx.check(st, "resume")
        was_stopped = task["stopped"]
        task["stopped"] = False
        task["stopped_by"] = None
        task["stopped_at"] = None
    if was_stopped:
        ctx.log(task, "resume")
    return _packet(ctx, task)


# ---------------------------------------------------------------- log and note


def _event_line(event: dict) -> str:
    who = f" by {event['by']}" if event.get("by") else ""
    detail = f" ({event['detail']})" if event.get("detail") and event.get("result") != "ok" else ""
    return f"{hhmm(event.get('at'))} {event.get('command', '?')}{who}: {event.get('result', '?')}{detail}"


def cmd_log(ctx: Ctx, args) -> str:
    task = ctx.find(ctx.load(), args.task)
    if task is None:
        return "No task yet. Nothing to show."
    events = ctx.store.read_timeline(task["id"])
    lines = [_event_line(e) for e in events]
    head = f"{task['id']} timeline ({len(events)} events):"
    kept: list[str] = []
    used = len(head)
    for line in reversed(lines):
        if used + len(line) + 1 > 1350:
            break
        kept.insert(0, line)
        used += len(line) + 1
    if len(kept) < len(lines):
        head = f"{task['id']} timeline (the last {len(kept)} of {len(events)} events; the rest is in .workflow/tasks/{task['id']}/timeline.jsonl):"
    return "\n".join([head, *kept])


def cmd_note(ctx: Ctx, args) -> str:
    task = ctx.find(ctx.load(), args.task)
    if task is None:
        return "No task yet. Nothing to note."
    events = ctx.store.read_timeline(task["id"])
    lines = [f"{task['id']} closing note: {short(task['goal'], 100)}"]
    if task["stage"] == "done":
        ship = task["ship"] or {}
        where = ship.get("url") or (f"merged as {short_sha(ship.get('merge_sha'))}" if ship.get("merge_sha") else "pushed")
        lines.append(f"Shipped at {short_sha(ship.get('sha'))}: {short(where, 120)}.")
    elif task["stage"] == "cancelled":
        quote = next((q for q in reversed(task["quotes"]) if q["kind"] == "cancel"), None)
        lines.append(f'Cancelled by {quote["by"]}: "{short(quote["text"], 120)}".' if quote else "Cancelled.")
    else:
        lines.append(f"Now at {task['stage']}.")
    end = task["ended_at"] or now()
    lines.append(f"Took {minutes(task['created_at'], end)} min from start to {task['stage'] if task['ended_at'] else 'now'}.")
    loops = task["fix_round"]
    lines.append(f"Loops back to build: {loops}." if loops else "Loops back to build: none.")
    asked = sum(1 for e in events if e.get("command") == "ask" and e.get("result") == "ok")
    refused = sum(1 for e in events if e.get("result") == "refused")
    posts: dict[str, list[dict]] = {}
    pickups: dict[str, str] = {}
    for event in events:
        if event.get("command") == "next" and event.get("key"):
            posts.setdefault(event["key"], []).append(event)
        if event.get("result") == "picked up" and event.get("key"):
            pickups.setdefault(event["key"], event["at"])
    repeated = sum(len(v) - 1 for v in posts.values())
    late = 0
    for key, posted in posts.items():
        if key in pickups and minutes(posted[0]["at"], pickups[key]) > LATE_PICKUP_MIN:
            late += 1
    lines.append(f"Questions to a person: {asked}. Refused commands: {refused}. Hand-offs posted again: {repeated}. "
                 f"Picked up after more than {LATE_PICKUP_MIN} min: {late}.")
    slips = [e for e in events if e.get("result") == "slip"]
    if slips:
        lines += bounded([f"Slip at {hhmm(e['at'])}: {short(e.get('detail', ''), 140)}" for e in slips], 500,
                         f".workflow/tasks/{task['id']}/timeline.jsonl")
    else:
        lines.append("Slips: none.")
    lines.append("Nothing needed from you.")
    return "\n".join(lines)


ROLE_NAMES = ROLES
