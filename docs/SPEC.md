# Preflight: spec v1

Written Wed 4 Nov 2026, after the Flip the Switch hackathon opened (00:01 IST).

## The one job

Take a team's project from **"we think we're done"** to **"verified, ready to submit"**,
inside a Switch room where agents and people finish the work together.

Teams lose contests and launches on avoidable misses: a missing README section, no
licence, a secret in git, a video that is too long. Not on code. Preflight is the room
that will not let that happen.

## The rule of the room

**The model proposes. Code decides.**

- Required checks are scripts (`preflight/`), with tests. They are deterministic.
- Agents read rules, explain, fix and review. They never decide "ready".
- "Ready" exists only when the script says so for the current commit (`preflight verdict`).
- A person (the Captain) approves the check list and the final "ready".

## Who is in the room

| Who | Job | Can be addressed by |
|---|---|---|
| **Captain** (human, exclusive role) | Approves the check list and the final verdict | n/a |
| **Teammates** (humans) | Ask, approve fixes, watch | n/a |
| `scout` (agent) | Reads a rules page, writes `rules-digest` and a draft `checks` as room documents | anyone |
| `inspector` (agent) | Runs the checks, posts the red/green table, refuses to say "ready" while a required check is red | anyone |
| `fixer` (agent) | Fixes red checks on a branch, asks permission for risky actions, opens a PR | owner and owner's agents |
| `reviewer` (agent) | Reviews the fixer's change independently, then scores the project with the public rubric (advisory only) | anyone |

## The flow (this is the demo)

1. Captain: "@scout here are the rules." Scout posts a draft check list. Captain approves it.
2. Captain: "@inspector run preflight." Six red checks. Inspector says **not ready**.
3. Captain: "@fixer fix the red checks you can." The fixer asks permission in chat before a risky
   git step (a Switch permission card). Captain **declines** the first request, then approves
   a safer one. Fixer adapts.
4. Fixer opens a PR. Reviewer finds one issue. Fixer amends. Inspector runs again: green.
5. Reviewer posts the advisory score (for example 61 to 88), with evidence.
6. **Fresh-agent test:** stop the fixer, invite a new agent, and it continues from the room documents alone.
7. Closing proof: we ran Preflight on this repository the night before we submitted.

## What is built (and where)

| Piece | Path |
|---|---|
| Check engine (Python, standard library only) | `preflight/` |
| Rulesets (`flip-the-switch`, `oss-release`) | `rules/` |
| Sample project with 6 planted defects (each with a natural fix) | `examples/` |
| Tests | `tests/` |
| Switch group template: agents + room + roles + documents + kickoff | `templates/` |
| Demo script and Switch feature map | `docs/` |

## Not in scope

A generic "AI dev team pipeline". An incident bot. A study bot. Anything that lets the model
overrule a failed check.

## Gates

| Gate | When (IST) | Pass condition |
|---|---|---|
| G1 | Wed 4 Nov 09:00 | This spec, demo script v0, cut list |
| G2 | Wed 4 Nov 22:30 | Walking skeleton: Discord message, scout, checks, inspector table |
| G3 | Thu 5 Nov 22:30 | Full demo flow passes 3 runs out of 3 |
| G4 | Fri 6 Nov 10:00 | Last full run. Bug fixes only after this |
| G5 | Fri 6 Nov 15:00 | Repo public, README final, video uploaded |
| G6 | Fri 6 Nov 18:00 | Submitted |
