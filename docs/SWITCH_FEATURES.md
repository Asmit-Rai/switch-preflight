# Why Switch is central: feature map

Preflight is a Switch room. Take Switch away and what is left is a script with no team around it.
This table lists every Switch feature the project relies on, where it lives in the repository, and
the scene in the demo that shows it (`docs/DEMO_SCRIPT.md`).

Tested on Switch Console 0.40.1 with the local Switch server it starts, a Discord server, and Claude
Code on macOS. "Verified" means we ran it ourselves during the hackathon.

| # | Switch feature | How Preflight uses it | Where | Demo scene | Verified |
|---|---|---|---|---|---|
| 1 | **Real chat app (Discord)** | The whole workflow runs in one Discord channel; people and agents are teammates in it | template `room.bridge` | all | yes |
| 2 | **Group template** (agents + room + roles + documents + kickoff in one YAML) | One import creates five agents and the room | `templates/preflight.template.yaml` | setup | yes (rebuilt from scratch at 01:32 on 4 Nov) |
| 3 | **Several agents, each with its own job and permissions** | `scout` (read, plan), `inspector` (run only), `fixer` (edit on a branch), `reviewer` (read only), `spare` | `agents:` in the template, `advanced_config.disallowedTools` | 2, 4, 5, 8 | yes |
| 4 | **Addressing policy** (`anyone` or `owner-agents`) | Anyone in the room may ask the inspector for a run; only the owner's agents can instruct the fixer | `agents[].addressing` | 4, 5 | yes (`anyone` is needed, or guests get one refusal then silence) |
| 5 | **Room aliases** | People and agents say `@scout`, `@inspector`, `@fixer`, `@reviewer`; agent instructions never hard-code a name | `room.aliases` | all | yes |
| 6 | **Roles, exclusive and shared** | `editor` is exclusive: exactly one agent may change the project at a time. `judge` is shared | `room.roles`, `assume_role` | 1, 5, 10 | yes |
| 7 | **Room documents as shared memory** | `rules-digest` (every rule with its quote), `checks` (the approved list and its fingerprint), `scorecard`, `runbook` | `room.docs`, agent tools `create_room_document`, `load_internal_documents` | 2, 3, 10 | yes |
| 8 | **Room instructions** | The rule of the room and the folder layout reach every agent when it joins, including a new one | `room.instructions` | 11 | yes |
| 9 | **Permission cards in chat** | Risky git steps (reset, rebase, push, rm) are answered by a person with `Allow once / Allow for this session / Decline`, and Switch records who answered | `examples/claude-settings.json` (`ask` rules), agent Auto mode | 6, 7 | yes (cards seen on Discord) |
| 10 | **Agent-to-agent handoff** | `scout -> inspector -> fixer -> reviewer` hand work to each other by alias inside the room | agent instructions, `send_targeted_message` | 5, 8 | yes |
| 11 | **Fresh-agent test / late joiner** | `!invite-agent @pf-spare` adds an agent with no background. It catches up from room instructions, the thread and the documents | `agents[].pf-spare`, `!invite-agent` | 11 | see `docs/DEMO_SCRIPT.md` |
| 12 | **Kickoff message** | The template posts a first message on the creator's behalf so the inspector proves the checker runs | `kickoff:` | setup | yes |
| 13 | **Provider choice** | The reviewer prefers a second provider (Codex, OpenCode) for an independent opinion and falls back to Claude Code | `params.reviewer_provider` | 8 | yes (fallback used on this machine) |
| 14 | **Room commands** | `!list-agents`, `!roles`, `!invite-agent`, `!agents-status`, `!reset` | built in | 1, 11 | yes |

## What Switch gave us that a plain script would not

- Several agents with separate permissions in **one conversation** a human can read and join.
- A person approving a risky step **in the same chat**, with the answer recorded.
- A **new agent** joining mid-work and catching up from the room, not from one person's laptop.
- Roles, so two agents never edit the same files at once.

## Honest limits (what we ran into)

- Agent instructions are stored exactly as written in Switch Console 0.40: curly-brace
  placeholders are not filled in for agents (they are for room instructions). The template is
  written around this.
- Documents seeded by a template cannot be edited by agents, so agents create the documents they
  need (`rules-digest`, `checks`, `scorecard`).
- Document contents cannot be read from Discord; an agent has to paste them.
- Claude Code's auto mode refuses to run freshly downloaded code. `scripts/setup-demo.sh` installs
  a narrow permission file (allow `kit/pf`; ask for risky git steps). A person installs it;
  agents never edit their own permissions.
