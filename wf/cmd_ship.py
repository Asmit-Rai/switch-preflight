"""The shipper's commands: ship and verify (plan Tasks 2 and 3).

`./wf ship` is the one command the hook turns into a permission card. It runs the gate again, writes the
ship token the pre-push hook checks, pushes and opens a pull request (or merges into main when there is
no remote), and deletes the token whatever happens.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .chat import short
from .context import Ctx
from .errors import GateClosed, Refused, WfError
from .gate import closed_line, gate, proof_chain, ready_line, shipped_where
from .machine import short_sha
from .timeutil import now


def shipper_turn(ctx: Ctx, task: dict) -> str:
    result = gate(ctx, task)
    if result.ok:
        return "\n".join([
            "Your turn: the gate is open. Post the next two lines, then run ./wf ship.",
            ready_line(task, result),
            'WAITING ON YOU: a permission card for "./wf ship" follows. Allow once to ship it.',
        ])
    return "\n".join([
        f"Your turn, but the {closed_line(result)[0].lower()}{closed_line(result)[1:]}",
        *[f"- {short(reason, 160)}" for reason in result.reasons[1:5]],
        "Nothing can ship until that is fixed. Post this and stop.",
    ])


def cmd_verify(ctx: Ctx, args) -> str:
    task = ctx.find(ctx.load(), args.task)
    if task is None:
        return "No task yet. Nothing to verify."
    if task["stage"] in ("done", "cancelled"):
        return "\n".join(proof_chain(ctx, task, None))
    result = gate(ctx, task)
    text = "\n".join(proof_chain(ctx, task, result))
    if not result.ok:
        raise GateClosed(text)
    return text


def github_slug(url: str | None) -> str | None:
    """`owner/repo` for a GitHub remote URL (https, ssh or scp form), else None."""
    if not url:
        return None
    match = re.match(r"^(?:https?://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else None


def pr_body(ctx: Ctx, task: dict, chain: list[str]) -> str:
    lines = [f"## {task['id']}: {short(task['goal'], 200)}", "", f"Asked by {task['by']} at {task['created_at']}.", "",
             f"### Done means (sha256 {task['spec']['sha256'][:12]})"]
    lines += [f"{item['n']}. {item['text']} [{item['check']}]" for item in task["spec"]["items"]]
    lines += ["", f"Assumed: {task['spec']['assumed']}", "", "### Plan"]
    for step in task["plan"]["steps"]:
        items = ", ".join(map(str, step["items"]))
        lines.append(f"{step['k']}. {step['text']} (items {items}, {step['type']}): `{step['command']}`")
    report = ctx.task_dir(task["id"]) / "test-report.md"
    lines += ["", "### Test report", ""]
    lines += report.read_text(encoding="utf-8").splitlines()[2:] if report.is_file() else ["(no report)"]
    lines += ["", "### Proof chain (./wf verify)", "", "```", *chain, "```", "",
              "Shipped by wf after a person allowed `./wf ship`."]
    return "\n".join(lines) + "\n"


def _open_pr(ctx: Ctx, task: dict, remote: str, body_path: Path) -> tuple[str | None, str]:
    """(link, note): the pull request when gh can open it, else the compare link and why not."""
    slug = github_slug(remote)
    main, branch = ctx.config.main, task["branch"]
    if slug is None:
        return None, f"pushed {branch} to origin; open a pull request there"
    compare = f"https://github.com/{slug}/compare/{main}...{branch}?expand=1"
    if shutil.which("gh") is None:
        return compare, "gh is not installed: open the pull request at the link"
    auth = subprocess.run(["gh", "auth", "status"], cwd=str(ctx.project), capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)
    if auth.returncode != 0:
        return compare, "gh is not logged in: open the pull request at the link"
    proc = subprocess.run(
        ["gh", "pr", "create", "--base", main, "--head", branch, "--title", f"{task['id']}: {short(task['goal'], 80)}",
         "--body-file", str(body_path)],
        cwd=str(ctx.project), capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    if proc.returncode == 0:
        urls = re.findall(r"https://\S+", proc.stdout)
        return (urls[-1] if urls else compare), "pull request opened"
    if "already exists" in proc.stderr:
        view = subprocess.run(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], cwd=str(ctx.project),
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        if view.returncode == 0 and view.stdout.strip():
            return view.stdout.strip(), "the pull request was already open"
    errors = [line for line in proc.stderr.splitlines() if line.strip()]
    return compare, f"gh pr create failed ({short(errors[-1] if errors else 'no message', 80)}): open it at the link"


def cmd_ship(ctx: Ctx, args) -> str:
    if bool(args.confirmed_by) != bool(args.quote):
        raise Refused('--confirmed-by and --quote go together: who said ship, and their words.')
    state = ctx.load()
    task = ctx.check(state, "ship")
    head = ctx.require_head(task, "ship")
    result = gate(ctx, task)
    if not result.ok:
        ctx.log(task, "ship", "gate closed", detail=short(result.reasons[0], 160))
        lines = [f"{task['id']}: {closed_line(result)}", *[f"- {short(r, 160)}" for r in result.reasons[1:5]],
                 "Nothing left the machine."]
        raise GateClosed("\n".join(lines))
    chain = proof_chain(ctx, task, result)
    folder = ctx.task_dir(task["id"]) / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    body_path = folder / "pr-body.md"
    body_path.write_text(pr_body(ctx, task, chain), encoding="utf-8")
    token = ctx.project / ".workflow" / "ship-token"
    remote = ctx.git.remote_url("origin")
    log: list[str] = []
    record = {"at": None, "sha": head, "how": None, "url": None, "merge_sha": None, "note": None, "remote": remote,
              "preflight": result.preflight, "confirmed_by": args.confirmed_by, "quote": args.quote}
    branch, main = task["branch"], ctx.config.main
    token.write_text(head + "\n", encoding="utf-8")
    try:
        if remote:
            pushed = ctx.git.run("push", "-u", "origin", f"{branch}:{branch}", check=False, timeout=300)
            log.append(f"$ git push -u origin {branch}\n{pushed.stdout}{pushed.stderr}")
            if pushed.returncode != 0:
                errors = [line for line in pushed.stderr.splitlines() if line.strip()]
                raise WfError(f"git push failed: {short(errors[-1] if errors else 'no message', 120)}. "
                              "Nothing was recorded. Try ./wf ship again, or ./wf ask.")
            url, note = _open_pr(ctx, task, remote, body_path)
            record.update(how="pr" if note.startswith(("pull request opened", "the pull request")) else "push", url=url, note=note)
            log.append(f"pull request: {url} ({note})")
        else:
            ctx.git.switch(main)
            merged = ctx.git.run("merge", "--no-ff", "-m", f"{task['id']}: merge {branch} ({short(task['goal'], 60)})", branch,
                                 check=False, env={"WF_SHIP": "1"})
            log.append(f"$ git merge --no-ff {branch}\n{merged.stdout}{merged.stderr}")
            if merged.returncode != 0:
                ctx.git.run("merge", "--abort", check=False)
                ctx.git.switch(branch)
                errors = [line for line in (merged.stderr + merged.stdout).splitlines() if line.strip()]
                raise WfError(f"The merge into {main} failed: {short(errors[-1] if errors else 'no message', 120)}. "
                              f"Nothing was merged; the folder is back on {branch}. A person looks at {main}.")
            record.update(how="merge", merge_sha=ctx.git.head(), note=f"merged into {main}")
    finally:
        token.unlink(missing_ok=True)
        (folder / "ship.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    record["at"] = now()
    with ctx.store.transaction() as st:
        task = st["tasks"][task["id"]]  # it shipped: record it whatever else happened meanwhile
        task["ship"] = record
        task["stage"] = "done"
        task["waiting"] = None
        task["stopped"] = False
        task["ended_at"] = record["at"]
        if st.get("active") == task["id"]:
            st["active"] = None
    ctx.log(task, "ship", by=args.confirmed_by, quote=args.quote)
    manager = ctx.alias("manager")
    if record["how"] == "merge":
        first = f"{task['id']} shipped: merged {branch} into {main} as {short_sha(record['merge_sha'])} (at {head[:7]})."
    elif record["how"] == "pr":
        first = f"{task['id']} shipped: pull request {record['url']} ({branch} at {head[:7]})."
    else:
        link = f" Open the pull request: {record['url']}" if record["url"] else ""
        first = f"{task['id']} shipped: pushed {branch} at {head[:7]} ({record['note']}).{link}"
    return f"{first}\nNext: {manager} posts the closing note"


__all__ = ["cmd_ship", "cmd_verify", "shipper_turn", "github_slug", "pr_body", "shipped_where"]
