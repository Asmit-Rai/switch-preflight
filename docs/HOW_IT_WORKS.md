# How Preflight works (one page)

Read this before you present or submit. The contest rules say you must be able to explain what you
submit. Everything here is true today and checked by running it.

## In one sentence

Preflight is a Discord room where AI agents and a person check a project until **a program** says it
is ready to submit. The agents help. The program decides. The person approves the risky steps.

## The idea to remember

**The model proposes. Code decides.**

An AI can say "all done" and be wrong. So the pass or fail of every check comes from a program with
tests, never from the AI's own words. The AI reads, explains, fixes and reviews. The person says yes
or no to anything risky.

## The parts

| Part | What it is | Why it matters |
|---|---|---|
| **Switch** | The tool that puts AI agents in a chat room like teammates | Without it there is no room, no roles, no approval cards |
| **Discord** | The chat app where we see the room | People and agents talk in one place |
| **Scout** (agent) | Reads the contest rules, drafts the check list | You approve the list; it gets a fingerprint |
| **Inspector** (agent) | Runs the checker and shows what it printed | It never says "ready" by itself |
| **Fixer** (agent) | Fixes red checks on a separate branch | Asks you before risky git steps |
| **Reviewer** (agent) | Reads the fix and gives advice | It cannot change anything or approve anything |
| **Spare** (agent) | Waits outside the room | Shows a new agent catching up from the room alone |
| **The checker** (`kit/pf`) | A Python program, 14 kinds of check, 520+ tests | This is what decides |
| **The gate** | `verdict` and `pack` commands | "Ready" only if the last run was READY for this exact commit, the tree is clean, the result is fresh and the check list is the one you approved |
| **The approval hook** | A small program that reads each shell command before it runs | Makes risky commands (git reset, push, rm...) ask you, however the agent words them |

## What happens, step by step

1. You tell the **Scout**: draft our check list from the contest rules. It reads the pages and asks
   you a few questions. You answer, then type `approve checks`. The list gets a fingerprint.
2. You tell the **Inspector**: run preflight. A table appears. Red means not ready.
3. The **Fixer** fixes what it can on a branch, one commit per check. When it wants to do something
   risky, Switch shows **you** a card with three buttons: Allow once, Allow for this session, Decline.
4. The **Reviewer** reads the change and says what it thinks. It is advice only.
5. You say `merge`. A card appears again. Then the Inspector runs again.
6. You say `ship it`. If the checker says READY for this commit, the gate opens and a
   `SUBMISSION.md` is written. If not, it tells you why it stays closed.

## What to say if someone asks

**Why Switch?** It gives us several agents with different permissions in one chat a person can read,
join and overrule, roles so two agents never edit the same files, documents that keep the approved
list in the room, and permission cards so a person approves risky steps in the chat.

**Why should we trust it?** We do not ask anyone to trust the AI. The checker is a program with
tests. The gate recomputes the verdict itself and refuses a file someone edited. The approved list has
a fingerprint. Risky commands need a person.

**What went wrong while building it?** Two real things. A merge ran without a card, because our first
rule matched only one way of writing the command; we replaced it with the approval hook and added
tests. And the Inspector once mistyped a number when copying the checker's output, then corrected
itself; that is why the gate is code, not chat.

**What did you reuse?** Switch, Claude Code, and the Discord connection (made on 3 Nov, before the
start). Everything else, including the repository, agents, room and template, was built after 00:01
IST on 4 Nov. The checker proves it: its first-commit check passes on this repository.

**What are the limits?** The hook reads command text; it is a guard, not a sandbox. A model can
mistype when it copies output. We tested Discord and Claude Code on a Mac only. A leaked secret must be
revoked at its source; cleaning git history does not undo it.

## Where things are

- Code: <https://github.com/Asmit-Rai/switch-preflight>
- The template that builds the room: `templates/preflight.template.yaml`
- Demo steps: `docs/DEMO_SCRIPT.md`; Switch features: `docs/SWITCH_FEATURES.md`
- The checker's own guide: `preflight/README.md`
