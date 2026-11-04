# Runbook: build Sketchroom with the six-agent team

The workflow is the product. Sketchroom is the test. A workflow bug is fixed in the template,
never in the app, and the part runs again (SPEC.md section 2.4).

Files here: `SPEC.md` (the spec), `team.template.yaml` (the six agents and the room),
`claude-settings.json` (what the agents may run), `setup.sh` (makes and resets the folder),
`BUGS.md` (the real bug log).

## 1. Before the first run (once)

- [ ] `python3 -m unittest tests.test_workflow` passes (the template and the script are checked).
- [ ] `sh workflow/setup.sh` makes `~/hackthone/sketchroom`: SPEC.md at one commit tagged `start-a`,
      and the permission file.
- [ ] Switch Console is open and its local server runs: `curl -s http://127.0.0.1:52258/health`.
- [ ] Switch Console: Templates > Import template > paste `workflow/team.template.yaml` > Use.
      Run 1 of Part A: keep the defaults. Create all. The kickoff wakes `@analyst`. Nothing to type.

## 2. During a run: a person does only four things

1. Answer the analyst's questions.
2. Reply `@analyst confirm brief`.
3. Reply `@planner approve 1-N` (N is the last step).
4. Merge, after the tester says all PASS and the advisor posted its note:
   `git -C ~/hackthone/sketchroom switch main`, then
   `git -C ~/hackthone/sketchroom merge --no-ff part-a-run1`, then
   `git -C ~/hackthone/sketchroom tag start-b` (the next part is cut from that tag).

Anything else a person has to do is a workflow bug.

## 3. A workflow bug: stop, log, fix the template, run again

1. Stop the run: `!interrupt @agent` in the room (or `!reset-all-agents`). Do not touch the app.
2. Add a row to `workflow/BUGS.md`. Quote what happened.
3. Fix `team.template.yaml` or `claude-settings.json`. Run `python3 -m unittest tests.test_workflow`.
4. Put the folder back at the start: `sh workflow/setup.sh --reset`. It deletes everything that
   git does not track (node_modules, dist ...), keeps the failed branch, and installs the latest
   permission file.
5. Switch Console: delete the old template, import the fixed file, and use new names:

   | Field | Run 1 | Run 2 | Run 3 |
   |---|---|---|---|
   | Name prefix | tdd | tdd2 | tdd3 |
   | Room name | sketchroom-a-run1 | sketchroom-a-run2 | sketchroom-a-run3 |
   | Run number | 1 | 2 | 3 |
   | Last task id | none | T1 | T2 |

6. When the rerun is clean, write its result in the row ("Rerun result"). Commit the fix:
   `git commit -am "workflow bug <n>: <fix>"`.

## 4. A clean Part A run is also the workflow check (SPEC.md 2.5)

Answer yes or no for each, with the evidence (a quote from the room):

- [ ] 1 Analyst: asked questions, got a confirmation, wrote the brief, handed off once, changed no file
- [ ] 2 Planner: numbered steps, each naming its test, waited for approval
- [ ] 3 Executor: red then green for every step, only the run branch, nothing outside the plan
- [ ] 4 Reviewer: tests first, then code, a clear approve or changes needed
- [ ] 5 Tester: clean install, PASS or FAIL per item with evidence
- [ ] 6 Advisor: closing note
- [ ] 7 Every hand-off woke the right agent on the first try
- [ ] 8 Catch check passed (below)

**Catch check.** On a throwaway branch cut from the passed run, in the shared folder:

1. `git switch -c catch-check part-a-run1`
2. Change the page heading in the code (not the test) so it no longer says "Sketchroom". Commit.
   Ask `@tester` and `@reviewer` to check `catch-check`. Both must report it.
3. Also edit the test to expect the wrong heading, so `npm test` passes. Commit.
   The tester must still FAIL the item. The reviewer must flag the edited test.
4. `git switch part-a-run1`, then `git branch -D catch-check`.

If either agent misses it, that is a workflow bug: log it, fix the template, run Part A again.

## 5. After Part A passes

Merge (step 2.4), then do Part B in a new room: Part `B`, Run number `1`, Last task id = the
last task of Part A. Part B needs the new tag `start-b`. Parts C and D work the same way.
