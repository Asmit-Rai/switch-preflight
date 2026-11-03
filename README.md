# Preflight

**A Switch room that will not let a team ship a broken submission.**
Built for the Flip the Switch hackathon (WeMakeDevs x DevTools Academy x Switch AI) by Team AXA.

> Demo video: _coming soon_ (this line gets the link before we submit)

Teams lose contests and launches on avoidable misses: a README section nobody wrote, no licence, a
secret in git history, a failing test, a video that is too long. Not on code. Preflight is a room in
Discord where AI agents and people work through that checklist together, until a program says the
project is ready.

**The model proposes. Code decides.** Agents read rules, explain, fix and review. Every pass or fail
comes from a deterministic checker with tests. "Ready" exists only when that checker says so, for the
exact commit, under a check list a person approved. A person also approves every risky step.

## Problem

A submission is judged on a short list of things (a public repo, a README that explains the setup, a
demo video, no leaked keys, tests that pass). Missing one costs more than a clever feature earns.
Humans skim this list at midnight and miss things. A lone AI agent is worse: it says "all done" in
confident prose, and nobody can tell which parts it actually checked.

## What it does

One Switch room, four agents, one human decision-maker (the Captain):

1. **Scout** reads the contest rules and drafts a check list. The Captain approves it. The approval
   is recorded with a fingerprint (a SHA-256 of the file), so nobody can quietly swap the list later.
2. **Inspector** runs the checker and pastes its output. It cannot say "ready" on its own.
3. **Fixer** fixes the red checks on a branch, one commit per check, and asks the Captain before any
   risky step (rewrite history, push, delete). The question is a Switch permission card.
4. **Reviewer** (read-only) reviews the fix and can score the project. Its score is advice only.
5. When the Captain says "ship it", the Inspector asks the gate: `GATE OK` only if the last run is
   READY for this exact commit, with a clean tree, a fresh result, and the approved fingerprint.
   Then it writes `SUBMISSION.md`.

A sixth agent, **Spare**, waits outside the room. The "fresh agent test": invite it and watch it catch
up from the room alone.

## Why Switch is central

Without Switch, Preflight is a script. With it, it is a team: several agents with different
permissions in one conversation a person can read, join and overrule. The 14 Switch features we use,
where each lives and which demo scene shows it, are in
[`docs/SWITCH_FEATURES.md`](docs/SWITCH_FEATURES.md). The short version:

| Switch feature | What it gives Preflight |
|---|---|
| Real chat app (Discord) | People and agents are teammates in one channel |
| Group template | One import creates five agents, the room, roles, documents and a kickoff message |
| Per-agent permissions and addressing | Inspector and Reviewer cannot edit files; only the owner's agents can instruct the Fixer |
| Roles (one exclusive) | Exactly one agent may change the project at a time (`editor`) |
| Room documents | The approved check list and its fingerprint live in the room, not on one laptop |
| Permission cards | A person answers risky steps in the chat, and Switch records who answered |
| Fresh-agent test | A new agent joins and catches up from the room alone |

## Agents we connected

All five run on **Claude Code**, on one Mac, started by Switch Console from one template
([`templates/preflight.template.yaml`](templates/preflight.template.yaml)). The Reviewer prefers a
second provider (Codex or OpenCode) for an independent opinion and falls back to Claude Code; this
machine has only Claude Code, so it used the fallback.

| Agent | Job | Who may address it | What it may not do |
|---|---|---|---|
| `pf-scout` | Reads rules, drafts the check list, records the approval | anyone in the room | edit files |
| `pf-inspector` | Runs the checker, shows its output unchanged, runs the gate | anyone | write or edit files |
| `pf-fixer` | Fixes red checks on a branch (holds the `editor` role) | the owner and the owner's agents | skip a permission card |
| `pf-reviewer` | Reviews the fix, scores the project, holds the `judge` role | anyone | change anything, or approve anything |
| `pf-spare` | Waits outside the room for the late-joiner test | anyone | (no background on purpose) |

## How the channel is set up

The room is mirrored into a Discord channel (`#preflight`). The template's `bridge` input finds the
Discord connection by name, then falls back to the server's default chat app.

To connect Discord to Switch (steps from the
[Switch docs](https://docs.switchagents.ai/switch-rooms/deploy/messaging-apps/discord)):

1. Discord Developer Portal: new application, add a bot, copy its token. Turn on the **Server
   Members** and **Message Content** intents.
2. Invite the bot with the scopes `bot` and `applications.commands` and the permissions View
   Channels, Send Messages, Send Messages in Threads, Manage Webhooks, Manage Channels, Manage Roles,
   Read Message History, Attach Files, Add Reactions.
3. Copy your server's ID (Developer Mode on, right-click the server icon).
4. Switch Console, your server, Messaging apps, Connect, Discord. Paste the token and the server ID.
5. Link your own Discord account ("Link my account"). The template's `{$creator}` is the Captain,
   and agents set to owner-only ignore an unlinked account.

The Discord server and bot connection used for the demo were created on 3 Nov, before the start. See
"Reused vs built" below.

## Run it

Needs: macOS, [Switch Console](https://docs.switchagents.ai/switch-rooms/getting-started/install-switch-console)
0.40.x running a local server, Claude Code installed and signed in, `git`, Python 3.11 or newer, and a
Discord connection as above. Tested on an Intel Mac with Switch Console 0.40.1.

```sh
git clone https://github.com/Asmit-Rai/switch-preflight
cd switch-preflight
sh scripts/setup-demo.sh          # makes ~/preflight-demo: the checker, the safety hook, a sample project
```

Then in Switch Console: **Templates, Import template**, paste
`templates/preflight.template.yaml`, **Use**, keep the defaults, **Create all**. Open `#preflight` in
Discord; the Inspector says hello and runs the checker.

Then follow [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md). The first message is:

> `@scout please draft our check list from the official contest rules, then ask me to approve it.`

What `setup-demo.sh` installs, and why a person (not an agent) does it:

- `kit/`: a clone of this repository, so every agent runs the same checker (`kit/pf`).
- `.claude/settings.json`: Claude Code's auto mode refuses to run freshly downloaded code, so this
  allows exactly one command (`kit/pf`) and registers the safety hook below. Agents never edit it.
- `project/`: a sample submission with 6 planted defects (`scripts/reset-demo.sh` makes a fresh one).

### Try the checker alone (no Switch needed)

```sh
python3 examples/make_sample.py /tmp/sample
python3 -m preflight run --project /tmp/sample --rules rules/flip-the-switch.toml --format table
python3 -m unittest discover -s tests          # 240+ tests, all offline, about 70 seconds
```

## Architecture

```mermaid
flowchart LR
  C["Captain and teammates<br/>(Discord)"] <-->|messages, approvals| R(("Switch room<br/>#preflight"))
  R <--> S[Scout]
  R <--> I[Inspector]
  R <--> F[Fixer]
  R <--> V[Reviewer]
  S -->|draft, then approved check list<br/>+ SHA-256 fingerprint| K{{"kit/pf checker<br/>code decides"}}
  I -->|run, verdict, pack| K
  F -->|commits on a branch| P[("project/<br/>git repo")]
  K -->|reads| P
  K -->|"verdict.json<br/>(commit, ruleset hash)"| G[Gate]
  G -->|GATE OK only if READY for this commit| SUB[SUBMISSION.md]
  H["approval hook<br/>hooks/risky_git.py"] -.->|risky command?<br/>ask a person| F
  H -.->|permission card| C
```

- **The checker** ([`preflight/`](preflight/README.md)): 14 check types (file, README headings,
  secret scan over files and history, first commit after the start, public repo, links, video length,
  a test command, ...), a gate, a pack writer and a linter. Standard library only.
- **The gate** recomputes the verdict from the results (a hand-edited file is refused), requires the
  commit to be HEAD, the tree to be clean, the result to be fresh, and the ruleset fingerprint to
  match the one the Captain approved.
- **The approval hook** ([`hooks/risky_git.py`](hooks/risky_git.py)) reads every shell command before
  it runs and makes risky ones (git reset, rebase, push, rm, ...) ask a person, however they are
  worded. It is a guard for an agent that is trying to follow the rules, not a sandbox.

## What happened when we ran it

On 4 Nov 2026 we ran the whole flow in Discord against the sample project. Each line below was checked
against files and git, not only against what an agent said:

- The Scout read the real rules pages and found the `/rules` page is only legal text. It reported 38
  rules (5 checkable by code, 33 needing a person), marked license, README sections and links as
  advisory because the official pages do not ask for them, and asked the Captain three questions. The
  Captain made them required again. The approved list got a fingerprint; our own SHA-256 matched it.
- The Inspector's table matched our own run of the checker line for line. Once it mistyped a number
  when copying the output ("0 warn" for "1 warn"), then caught and corrected it in its next message.
  Retyping is a real weakness of any model; see Limits.
- The Fixer fixed the failing test by changing the code, not the test, and asked the Captain which
  licence and copyright holder to use instead of guessing.
- It asked to rewrite git history to remove a leaked token. The Captain tapped **Decline**: the
  command did not run (checked on disk) and the Fixer reported which check stayed red because of it.
  After the Captain changed their mind, a second card was **allowed once**, and the token was gone
  from the branch history.
- The Reviewer's findings made the Fixer add a follow-up commit. The Inspector then reported READY
  for the final commit.
- One thing went wrong, and we fixed it: the merge step (`git reset --hard`) ran with **no card**.
  Our first permission rule only matched commands that start with `git reset`, and the Fixer wrote
  `git -C /path reset`. Whether a card appeared had depended on how the agent worded the command. We
  replaced the rule with the approval hook, which reads the command itself, and pinned it with 11
  tests that cover many spellings.

## Reused vs built during the hackathon

Built after the hackathon opened (00:01 IST, 4 Nov 2026; the first commit is at 00:08): this
repository, the checker, the rulesets, the sample project, the approval hook, the Switch template,
the five agents, the room and the workflow. Every commit is dated after the start, and the checker's
`first-commit-after-start` check proves it on this repository.

Reused, and made before the start:

- **Switch itself** (Switch Console, the Switch server) and **Claude Code**, as the rules allow.
- **The Discord server and the "Agent Switch" bot connection** were created on 3 Nov, and a few
  trial agents (Analyst, Planner, Executor, Reviewer, Tester, Advisor) were made that day. They are
  not part of Preflight and are not in its room. Connecting a chat app is infrastructure, like
  installing Switch Console; we are listing it so nobody has to guess.
- Official Switch documentation and examples, which we read to write the template.

## Limits

- The Inspector retypes the checker's output into chat, and a model can mistype. The gate (`verdict`)
  is code, so a typo cannot make a project ready, but a person should compare a table with the
  checker's own output when it matters.
- Switch Console 0.40 stores agent instructions exactly as written, so curly-brace placeholders are
  not filled in for agents (they are for room instructions). The template is written around that.
- A template cannot give agents edit rights on documents it seeds, so agents create the documents
  they need (`rules-digest`, `checks`, `scorecard`).
- The approval hook reads command text. It cannot see inside a script or `python3 -c` code.
- A secret that was ever pushed is compromised. Purging history does not undo that; revoke the key.
- We tested Discord and Claude Code on macOS. We did not test Slack, Teams or Mattermost.

## More documents

- [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md): one page, in plain words
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md): the exact messages to type, scene by scene
- [`docs/SWITCH_FEATURES.md`](docs/SWITCH_FEATURES.md): every Switch feature we use and where
- [`docs/SPEC.md`](docs/SPEC.md): the plan we wrote at the start
- [`preflight/README.md`](preflight/README.md): the checker's guide (check types, exit codes, schema)

## Team and licence

Team AXA: Asmit Rai (team leader) and Aman Gupta. The submission is made by a person, as the rules
require. This project was built with AI help (an AI assistant running Claude and several Claude Code
sessions), as the rules allow.

Released under the [MIT licence](LICENSE).
