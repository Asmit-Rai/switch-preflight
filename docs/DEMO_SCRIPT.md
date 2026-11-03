# Demo script (about 3 minutes)

The exact messages to type, in order. Everything runs in one Discord channel (`#preflight`).
The Captain is the person who created the room. **The Captain taps the permission cards.**

Before you start (not on camera):

1. `sh scripts/setup-demo.sh` (once), then use the Switch template (README, "Run it").
2. `sh scripts/reset-demo.sh` for a fresh sample project with 6 planted defects.
3. In Switch Console, check the Fixer's **Auto mode** is ON (default). Risky git steps then
   show a permission card because of the rules in `.claude/settings.json`.
4. Have the approved check list ready. If the room has no `checks` document yet, do step 1 below
   first (it takes about 4 minutes, so for a short video record it once and cut).

## Scenes

| # | Say or type (Discord) | What the viewer sees | Switch feature on show |
|---|---|---|---|
| 1 | `!list-agents` then `!roles` | Four agents, two jobs (`editor` is exclusive and held by the fixer, `judge` is shared) | Agents in a real chat app, roles |
| 2 | `@scout please draft our check list from the official contest rules, then ask me to approve it.` | Scout reads the rules page, writes `rules-digest`, posts a short summary and asks for approval | Agent works from a link, room documents |
| 3 | `approve checks` (reply to the scout) | Scout saves the room document `checks` with the **fingerprint**. Inspector can verify it | Human approval in chat; documents as shared memory |
| 4 | `@inspector run preflight` | A red/green table: 6 red checks, `VERDICT: NOT READY`. The inspector pastes it unchanged | Agent addressed by alias; the code decides |
| 5 | `@fixer fix the red checks you can` | Fixer takes the `editor` role, makes a branch, one commit per check id | Exclusive role, agent-to-agent handoff |
| 6 | (a card appears) the Fixer wants to rewrite history to remove the leaked token | **Permission card** with `Allow once`, `Allow for this session`, `Decline`. The Captain taps **Decline** first | Human-in-the-loop approvals |
| 7 | (after decline) the Fixer says which check stays red and offers options. Captain: `Go ahead, but only this once.` then taps **Allow once** on the new card | The Fixer does the safer thing after a decline, then the approved thing | Approval gate in the demo |
| 8 | `@reviewer please review the fix branch` | Reviewer reads the diff, gives findings with file and line. It cannot approve | Second opinion; read-only agent |
| 9 | `@fixer merge` (Captain taps the card if one appears) then `@inspector run preflight` | `VERDICT: READY` is only possible now | Code-decided readiness |
| 10 | `@reviewer score it` | An advisory score with quoted evidence. "Advisory only. The checks decide." | Judge role, room document `scorecard` |
| 11 | `!invite-agent @pf-spare` then `@pf-spare what is this room for and what has been decided so far?` | A brand-new agent catches up from the room alone and names the documents it used | **Fresh-agent test** / late joiner |
| 12 | `@inspector ship it` | `GATE OK` and a `SUBMISSION.md` pack. Try it first on the broken project: `GATE CLOSED` | Code-enforced gate |

## Lines to say (voice-over)

- Hook: "Teams do not lose on code. They lose on the checklist."
- After scene 4: "Notice what the inspector did not do: decide. The checker did. The model proposes, code decides."
- After scene 6: "The agent cannot rewrite history without me. Switch asks me in chat, with who answered recorded."
- After scene 9: "Ready is not a vibe. The gate only opens for this exact commit, with the ruleset I approved."
- After scene 11: "A new agent, no briefing. The room is the memory."
- Close: "One template, one command to set up, runs on any chat Switch supports. Same room, different rules file: we also ship an open-source release checklist."

## If something goes wrong on camera

- An agent is slow: say "Working..." is the point, then cut. Never fake the output.
- A card does not appear: the Fixer will say a step was refused; hand it to the Captain and carry on. That is an honest-failure moment, keep it.
- Discord drops a long reply: ask the agent "post that in two shorter messages".
