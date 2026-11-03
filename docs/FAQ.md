# FAQ: how Preflight handles real projects

Six questions a team asks before it trusts a room of agents with its repo, and how Preflight answers
each one. Commands run from the agents' shared folder (`~/preflight-demo`, made by
`scripts/setup-demo.sh`).

## 1. How does an agent know which repo to work on, and where to clone it?

An agent never searches your disk. All agents start in one shared folder, and the project under test
is always `project/` inside it. One command decides what is in there:

```sh
kit/pf target https://github.com/owner/repo      # clone a GitHub repo
kit/pf target ~/code/my-app --branch dev         # copy a local repo (your folder is never changed)
kit/pf target sample                             # the demo sample with 6 planted defects
kit/pf target --show                             # what is loaded now, and at which commit
```

- It clones into a temporary folder first, so a failed clone changes nothing. The old project is
  moved to `.old-projects/`, never deleted.
- It writes down what it loaded (`project/.preflight/target.json`), so every agent, and a new agent
  that joins later, can ask `--show` instead of guessing.
- In the room, a person asks: `@fixer switch the project to https://github.com/owner/repo`. The
  approval hook makes `pf target` show the Captain a permission card first.
- It refuses unsafe sources: URL schemes other than https and ssh (http, git, file, ext), a URL with
  a password or token in it, and text that starts with `-` or holds spaces. It never waits for a
  password: a private repo needs `gh auth login` on the machine, or a GitHub connection granted to
  the agent in Switch Console.
- To set the project once at setup: `scripts/setup-demo.sh ~/preflight-demo https://github.com/owner/repo`.

**Contributing to someone else's project:** fork it on GitHub, then `kit/pf target <your fork>`. The
Fixer works on a branch, one commit per fix. When the Captain says "open a pull request", it pushes
the branch and runs `gh pr create`. Both steps show a permission card, because the hook treats every
push and every GitHub write (`gh pr create`, `gh issue create`, `gh repo fork`...) as risky. Read-only
`gh` commands (`gh pr view`, `gh pr list`...) run without asking.

## 2. How do the agents talk to each other?

In the room, by `@alias`, so every handoff is visible in Discord. Switch routes the mention to the
agent; nothing goes through a private side channel.

| From | To | Message |
|---|---|---|
| Scout | Inspector | "Checks approved by <person>. Fingerprint <hex>. @inspector please run preflight." |
| Inspector | Fixer | "@fixer please fix <check ids>" |
| Fixer | Inspector, Reviewer | "@inspector please run preflight", "@reviewer please review <branch>" |
| Reviewer | Fixer | "Review of <branch>: changes requested", with file:line findings |

Room rules that keep this clean (in the template's room instructions):

- **One handoff is one message**, with its evidence: a branch, a commit or check ids.
- **At most two rounds** of fixing the same check or reviewing the same branch. After that, the
  Captain decides. This stops two agents from looping.
- **No replies to thanks or acknowledgements**, and no mentions just to agree.
- Only a person approves: the check list, a risky step, and "ready". An agent's "yes" is never an
  approval.
- The `editor` role is exclusive, so only one agent changes `project/` at a time.

The Reviewer gives a more independent opinion when it runs on a different coding agent. The template
already prefers Codex or OpenCode for it and falls back to Claude Code. Install one of them on the
machine and the next template run picks it up.

## 3. How does a person see that it is really done?

With files the checker makes, never with a screenshot of a screen (a screen can show a token).

- **`report.html`**: one self-contained page with a big READY or NOT READY, the commit (a link when
  it is on GitHub), the approved check list's fingerprint, and every check with its detail and fix.
  `kit/pf run ... --html report.html` makes it for any run.
- **`SUBMISSION.md`** plus the report: `kit/pf pack ... --out SUBMISSION.md --html report.html`
  writes them only while the gate says `GATE OK`.
- **In Discord**: at "ship it" the Inspector attaches both files to the room (Switch's
  `send_attachment`), so the proof sits in the chat next to the decision.
- **A public link**: GitHub Actions runs Preflight on this repository on every push. The badge in
  the README links to the run, and the run page shows the full check table.

Deploying the project is not needed for this contest (it asks for a repo, a README and a video).
For a web project, Switch's Vercel connector can give an agent deploy access; the same rule applies:
a person approves first.

## 4. Do we have connectors, for example Jira tickets, documents or PDFs?

Yes, without writing code. Switch ships connectors for GitHub, GitLab, Bitbucket, Jira and
Confluence (Atlassian), Linear, Asana, Notion, Google Workspace, Microsoft 365, Box, Salesforce,
Datadog, New Relic, Canva and Vercel.

1. Switch Console, Settings, Connections: connect the service once (read only, or read and write).
2. Grant it only to the agent that needs it (for tickets, the Inspector).
3. In the room: `@inspector open a Jira ticket for each red check that needs a person`.

The agent acts as the person who connected the service, and Switch makes it ask before it uses the
service's tools. The room rule adds: create or change something only when a person asked for it in
the room, at most one ticket per red check.

Documents in: attach the rules as a PDF or text file to a Discord message and ask
`@scout use the attached rules`. The Scout downloads it (`download_attachment`) and reads it.
Documents out: `SUBMISSION.md` and `report.html` (question 3). Room documents (`rules-digest`,
`checks`, `scorecard`) keep the shared memory; ask an agent to attach one to read it in Discord.

## 5. Which tools do the agents use (MCP, Chrome, tests, dev tools)?

| Agent | Tools | Not allowed |
|---|---|---|
| Scout | WebFetch to read rules pages, a browser only for pages that need JavaScript, `kit/pf lint` and `sha` | Edit; writing outside `rules/` |
| Inspector | `kit/pf run`, `verdict`, `pack`, `doctor` | Write, Edit |
| Fixer | Read, Edit, Bash, git, `kit/pf target` | risky commands without a permission card |
| Reviewer | Read-only git (`diff`, `log`, `show`) | Write, Edit |

- Every agent also gets Switch's own MCP server (`switch`): room messages, documents, roles,
  attachments, permission cards.
- **Tests:** the `tests-pass` check runs the project's own test command with a time limit. A failing
  test must be fixed in the code; the Reviewer rejects a weakened test.
- **Safety:** `hooks/risky_git.py` reads every shell command before it runs and makes the risky ones
  ask a person, however they are written: git history rewrites, pushes, deletes, `pf target`,
  GitHub writes through `gh`, and the setup and reset scripts.
- **Before a demo:** `kit/pf doctor` checks Python, git, Claude Code, the GitHub login, the
  permission file, that the approval hook really asks, the approved check list, and (with
  `--switch-url`) the Switch server. Each problem comes with a one-line fix. In the room:
  `@inspector doctor`. The template's kickoff runs it too.

## 6. What about DevOps?

- **CI:** `.github/workflows/ci.yml` runs every test on Linux and macOS, on Python 3.11 and 3.14, for
  each push and pull request.
- **Dogfooding:** `.github/workflows/preflight.yml` runs Preflight on this repository against the
  contest rules on each push to `main`, writes the check table to the run page, and keeps the HTML
  report as an artifact.
- **Use it as a merge gate in any repo:** the same checks the room approved can block a bad merge.

  ```yaml
  - uses: actions/checkout@v7
    with:
      fetch-depth: 0               # the secret scan and the first-commit check read all history
  - uses: Asmit-Rai/switch-preflight@main
    with:
      rules: rules/approved.toml   # the check list the Captain approved in the room
      approved-sha: b691e62a63c2   # optional: fail unless it is exactly that list
  ```

- **Nothing to host:** Switch Console runs the server and the agents on your own machine. Setup is
  one script (`scripts/setup-demo.sh`), a fresh demo is one command (`kit/pf target sample`), and
  `kit/pf doctor` tells you what is broken before anyone else sees it.
- **Secrets:** never in the repo (`.gitignore`, and the secret scan reads every file and all git
  history). A key that was ever pushed is compromised: revoke it, then purge it.
