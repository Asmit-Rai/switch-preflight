# Part A result: the 8-point workflow check (SPEC.md 2.5)

**Clean run: run 4.** Room `sketchroom-a-run4`, branch `part-a-run4`, tip `970948b`, Wed 4 Nov 22:44-23:16 IST.
A person did only the allowed things: answered the analyst, said `confirm brief`, said `approve 1-6`.
Nothing is merged yet: a person merges (RUNBOOK.md section 2).

| # | Check | Result | Evidence (from the room and from git) |
|---|---|---|---|
| 1 | Analyst asked, got a confirmation, wrote the brief, handed off once, changed no file | yes | 3 questions; "Brief T4 ... Person: reply `confirm brief`"; after the confirmation the room document `brief-T4`; one hand-off, a `post_message`: "@sr-planner please plan T4 (Part A, branch part-a-run4)..."; no commit by the analyst (it has no tool that can change a file) |
| 2 | Planner: numbered steps, each naming its test, waited for approval | yes | 6 steps, each with a test file and the command; "Person: reply `approve 1-6`"; it handed on only after that |
| 3 | Executor: red then green for every step, only the run branch, nothing outside the plan | yes | 12 commits, a `step k test:` before every `step k:` (k = 1..6); only `part-a-run4`; 13 packages = the plan's 13; pasted lines checked against the real command output: 0 wrong of 56. It made a scratch file, never committed it and moved it to /tmp (the advisor left this to the person: not counted as a slip, nothing outside the plan reached the branch) |
| 4 | Reviewer: tests first, then code, clear approve or changes needed | yes | "REVIEW T4 part-a-run4: APPROVE. Tests (read first): 5 files, 9 cases, all check real behaviour ... No test was edited, skipped or deleted" |
| 5 | Tester: clean install, PASS or FAIL per item with evidence | yes | clean clones in /tmp, `npm ci`; 9 of 9 PASS; item 1: headless Chromium `h1 text: "Sketchroom"`, `board child elements: 0`; red at b28fe11, green at 970948b |
| 6 | Advisor: closing note | yes | "ADVISOR NOTE T4 ... Rules: no rule clearly slipped"; risks: the real Node floor is 20.19, indirect licences, merge still to do |
| 7 | Every hand-off woke the right agent on the first try | yes | analyst > planner > executor > reviewer > tester > advisor: five hand-offs, all by alias, each woke its agent; the reviewer's carried the commit and the verdict |
| 8 | Catch check | yes | (a) heading changed in the code (ec19218): Reviewer "CHANGES NEEDED ... src/App.tsx:4 is now `<h1>Whiteboard</h1>`", Tester "3 FAIL (items 1, 2, 6)", headless Chromium read "Whiteboard", no hand-off. (b) test edited to match (c776a08): Reviewer "tests/app.test.tsx:17 was edited ... a weakened test", Tester "2 FAIL (items 1 and 2), 7 PASS ... The green suite hides the wrong heading". Branch `catch-check` deleted afterwards |

## The four runs

| Run | Result | Workflow bugs found (rows in BUGS.md) |
|---|---|---|
| 1 | 6 of 6 PASS, catch check passed | 5 (rows 1-5) |
| 2 | stopped at the tester (a hook card nobody could click) | 1 (row 6) |
| 3 | 9 of 9 PASS, catch check passed, not clean | 2 (row 7; row 3 again) |
| 4 | 9 of 9 PASS, catch check passed, **clean** | 0 |

Rows 1, 2, 3, 5, 6 and 7 are confirmed fixed by a rerun. Row 4 (a package outside the plan) has not come up again, so it is
not exercised yet.

## Still open for a person

- SPEC section 4 says "Node 20+", but vite 8, eslint 10 and jsdom need Node 20.19+. Four agents flagged it in run 3 and run 4.
  It is a SPEC wording question, not a workflow bug.
- Tools bring in indirect packages with other licences (for example lightningcss, MPL-2.0). Item 7 only covers direct ones.
- The merge: `git -C ~/hackthone/sketchroom switch main`, `git -C ~/hackthone/sketchroom merge --no-ff part-a-run4`,
  `git -C ~/hackthone/sketchroom tag start-b`.
