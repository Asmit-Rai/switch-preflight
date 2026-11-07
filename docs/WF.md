# wf: the build room (runbook)

wf is a small command-line tool and a Switch room of seven agents.
You say what you want built. The agents write the spec, plan it, build it test first, review it,
test it from a clean clone, and ship it. You click once.
The model proposes, code decides: `./wf` holds the task, the rules, the evidence and the ship gate.

## 1. Setup, once per project

From this repository (the kit), then in your project (a git repository with a `main` branch):

```sh
python3 -m wf init ~/myproject
cd ~/myproject && ./wf doctor
```

- `wf init` makes `.workflow/` (state, evidence, `project.toml`) and the `./wf` launcher.
  It installs the git hooks and merges `.claude/settings.json` (it keeps your entries and writes a `.bak`).
  It keeps `.playwright-mcp/` (the browser tool's output) out of `git status`, so a screenshot never makes the tree unclean.
  Running it again only adds what is missing (a project set up before this adds that one line).
- `./wf doctor` checks the setup. It prints one fix per problem, or `Ready. Tell me what you want built.`
- Check `.workflow/project.toml`: `install` (for example `npm ci`), `suite` (the commands that must pass),
  `ports`, `main`, `max_loops`. Fix any guess that is wrong.

## 2. Before the first import: five checks

1. Delete or rename any agent on the server named like an alias: manager, specifier, planner, builder,
   reviewer, tester, shipper. If one exists, Switch warns on every mention.
2. `wf init` ran and `./wf doctor` says Ready.
3. Your Discord account is linked (Switch Console, your server, Messaging apps, Link my account).
   The shipper answers only its owner, so only a linked owner can click the ship card.
4. Playwright browsers are installed once (`npx playwright install chromium`), if the project has browser items.
5. Nothing holds the ports in `.workflow/project.toml` (for each port, `lsof -ti :4321` prints nothing).

## 3. Import the template

1. Switch Console: Templates, Import template, paste `templates/wf.template.yaml`, Use.
2. Project folder: the folder you ran `wf init` on. Keep the other defaults.
3. Create all. You get seven agents (`wf-manager` and so on) and one room.
4. The kickoff says `@wf-manager hello`. The manager runs `./wf doctor` and posts the answer.
   In the room, use the short names, like `@manager`.

## 4. What you say to the manager

You talk only to the manager. One task at a time.

- `@manager add Ctrl+D to duplicate the selected shape`: starts a task.
- `@manager status`: where the task is, and whether anything is needed from you.
- `@manager stop`: pause. `@manager resume`: go on (it posts the hand-off again).
- `@manager change: use 20px`: before the build starts, the spec is written again with your words.
- `@manager ok`, or your answer in your own words: when a WAITING ON YOU line is open.
- `@manager ship`: after you declined the ship card, when you are ready.
- `@manager cancel`: ends the task. The branch stays. Nothing has left the machine.

Answer the ship card with its buttons. Never send a message that is only yes or no:
Switch reads a bare word as the answer to an open card.

## 5. What a normal task looks like

Each post is short. The first line says what matters. The last line says what is next.

1. Manager: `T7 started: add Ctrl+D ... Branch t7-ctrl-d-duplicate.`, then the hand-off to the specifier.
2. Specifier: `Spec T7 ready: 4 items`, the items, and what it assumed. Then the hand-off to the planner.
3. Planner: `Plan T7 ready: 4 steps for 4 items`. Then the hand-off to the builder.
4. Builder: per step, a failing test, then a passing one. Then `./wf build done` and the hand-off to the reviewer.
5. Reviewer: the verdict. APPROVE goes to the tester. CHANGES go back to the builder.
6. Tester: PASS or FAIL for every item, each with one line of its own evidence. Then the hand-off to the shipper.
7. Shipper: `T7 is ready to ship: ...` and a permission card for `./wf ship`.
   You click Allow once. The shipper posts the pull request link (or the merge into `main`).
8. Manager: the closing note: what was built, loops, questions, slips. `Nothing needed from you.`

That one click is all you do in a normal task.

## 6. When things go wrong

- **An agent says "Not your turn".** Someone tagged it early. Nothing to do.
- **WAITING ON YOU.** An agent is stuck with no safe default. Tell the manager `ok` (the default) or your answer.
- **Loops.** After 3 rounds of review changes or test FAIL, WAITING ON YOU asks: continue or cancel.
  Continue gives one more round. Cancel ends the task and keeps the branch.
- **A hand-off nobody picked up.** `@manager status` says "not picked up (14 min)". Say `@manager resume`.
- **An agent was reset** (`!reset @wf-builder`, a crash, a new session). It runs `./wf status` and goes on.
- **HEAD moved after build done** (a commit made outside wf). Review, test and ship refuse.
  The builder runs `./wf build done` again. The closing note names the slip.
- **You declined the ship card.** Nothing ran. Nothing left the machine. Say `@manager ship` when ready.
- **A command was refused.** The agent runs `./wf ask` naming the command it needed, so WAITING ON YOU is open and
  `./wf status` shows it, then stops. Read the reason, fix the setup, then tell the manager `ok` or what to do.
- **A port is held.** The agent asks you; it never kills what it did not start. Stop your own server,
  then tell the manager `ok`.
- **An agent broke a rule.** The closing note names the slip. Fix the skill,
  the template or wf, never the app.

## 7. The guards, and their limits

- **wf itself** (every coding agent): a command out of order is refused (exit 2) and nothing changes.
  The ship gate (exit 3) needs review APPROVE and every item PASS at the branch tip, a clean tree,
  the suite green, and Preflight READY when a ruleset is set.
- **The approval hook** (Claude Code only): `hooks/risky_git.py --policy wf`, installed by `wf init`.
  It refuses deletes, history changes, branch switches, raw git add, commit, stash, merge and push,
  and publishing. It turns `./wf ship` into a permission card.
- **Git hooks** (every coding agent): pre-commit refuses `node_modules`, build output and any commit on `main`.
  pre-push refuses unless `./wf ship` is the one pushing.
- **Limits.** This is not a sandbox. The guards stop an agent that tries to follow the rules, not a hostile one.
  The hook reads command text; it cannot see inside a script. `--no-verify` bypasses git hooks.
  Codex and OpenCode get only wf and the git hooks. On GitHub, protect `main`: require a pull request,
  and allow no force push.
- **No card in your chat app?** The fallback is weaker: a person runs
  `./wf ship --confirmed-by <name> --quote "ship it"` in a terminal, and wf records who said it.

## 8. No chat app at all

You are the seven agents. Run the same commands in a terminal, in the project folder.
`./wf skill <agent>` prints how each stage is done.

```sh
./wf new "add Ctrl+D to duplicate the selected shape" --by asmit
./wf spec submit <<'EOF'
1. Ctrl+D with one shape selected adds a copy 10px right and down [unit]
Assumed: offset 10px
EOF
./wf plan submit <<'EOF'
1. duplicate one shape | items 1 | unit | tests/duplicate.test.ts | npx vitest run tests/duplicate.test.ts
EOF
./wf build start
# write the test, then:
./wf run --step 1 --red
./wf commit --step 1 --test -m "Ctrl+D copies one shape" -- tests/duplicate.test.ts
# write the code, then:
./wf run --step 1 --green
./wf commit --step 1 -m "Ctrl+D copies one shape" -- src/duplicate.ts
./wf run --suite
./wf build done
./wf review approve --findings "the test checks the real copy; nothing extra"
./wf test start
./wf test item 1 PASS --evidence "npx vitest run tests/duplicate.test.ts: Tests 1 passed (1)"
./wf test done
./wf ship
```

`./wf status` says where you are at any moment. `./wf next` says who goes next.

## 9. For judges: `./wf verify`

`./wf verify T7` prints the whole proof chain of a task. It changes nothing.
It shows the spec's SHA-256, that the plan covers every item, the commit order (each test before its code),
the red and green evidence, the review at its commit, the test results at the same commit,
Preflight, and the ship record. For a shipped task it says `shipped at <sha>`.
The same text is in the pull request body, so you can read it on GitHub too.
`./wf log T7` is the timeline in plain words. `./wf note T7` is the closing note.
