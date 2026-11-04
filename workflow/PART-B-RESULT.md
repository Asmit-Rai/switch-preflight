# Part B result, run 1: the workflow check (SPEC.md 2.5, points 1-7)

**Run 1 passed: 13 of 13 items and the "Also" line.** Room `sketchroom-b-run1`, branch `part-b-run1`, tip `e7e1041`,
Thu 5 Nov 00:28-02:28 IST. No workflow bug found (BUGS.md is unchanged).
Merged on Asmit's word (Thu 5 Nov 02:35, "do all of it"): Sketchroom `main` = `19a728d` (--no-ff merge of `part-b-run1`,
same tree as `e7e1041`). The SPEC changes below are committed on top as `6b9862d`, and the tag `start-c` is on that commit.

Who typed what: the three person messages (`@sr-analyst ok`, `@sr-analyst confirm brief`, `@sr-planner approve 1-22`)
were typed in Discord by the assistant (a Mac helper session, using Asmit's logged-in Discord in Brave), word for word
what a person would type. The agents' work needed nothing else from a person, except the port question below.

| # | Check | Result | Evidence (from the room, git and the agents' session logs) |
|---|---|---|---|
| 1 | Analyst asked, got a confirmation, wrote the brief, handed off once, changed no file | yes | 3 questions (freehand saved as an open polyline; Playwright, Chromium only, `npm run test:e2e`, not in `npm test`; bundled `@fontsource/caveat`); brief T5 = the 10 SPEC items word for word + 3 "(section 4)" items (licences in the README, offline, SVG + roughjs); after `confirm brief` the room document `brief-T5` and one hand-off `@sr-planner please plan T5 ...` (post_message, alias in the text); no Write/Edit tool use in its session |
| 2 | Planner: numbered steps, each naming its test, waited for approval | yes | 22 steps, each with a test file and the command, covering all 13 items; step 22 = `e2e/smoke.spec.ts`; it handed on only after `approve 1-22`; it asked about zoom, pan, colour swatches, fill and stroke width (no item needs them) and did not plan them; no file written |
| 3 | Executor: red then green for every step, only the run branch, nothing outside the plan | yes | 46 commits = 22 `step k test:` + 22 `step k:` + 2 review-fix commits. I checked the order with git: for k = 1..22 the test commit comes first, and the fix test `791e1c7` comes before the fix `e7e1041`. Only `part-b-run1`. No test file deleted, 0 test lines removed against `start-b`, no `.skip`/`.only`/`.todo`. 4 new packages (roughjs, zustand, @fontsource/caveat, @playwright/test) each in the README with its licence. paste_check: 25 posts, 316 lines, 0 not found |
| 4 | Reviewer: tests first, then code, clear approve or changes needed | yes | First review **CHANGES NEEDED** with file and line: `src/persist.ts:59`, the backup write in `loadScene` has no try/catch, a full storage while loading a corrupt save blanks the page (item 5 says no crash); fix to be written test first. After the fix, **APPROVE at `e7e1041`** (02:20). It also checked and accepted the two test-setup changes |
| 5 | Tester: clean install, PASS or FAIL per item with evidence | yes | Clean copies under /tmp, `npm ci`; 133 tests, lint, typecheck, build all green; `npm run test:e2e` passes; its own real-browser checks (storage full, corrupt save, corrupt save + full storage, export/import, cookies empty, 18 requests none outside 127.0.0.1, no `<canvas>` even after a PNG export); 13 of 13 + Also PASS. paste_check: 3 posts, 39 lines, 0 not found. It edited only its own script in /tmp, nothing in the repo |
| 6 | Advisor: closing note | yes | "ADVISOR NOTE T5 (part-b-run1 at e7e1041)": what went well, 2 slips, risks for Part C |
| 7 | Every hand-off woke the right agent on the first try | yes | analyst > planner > executor > reviewer > executor (fix) > reviewer > tester > advisor, all by alias, each woke its agent once (advisor: "Each handoff woke the right agent once") |
| 8 | Catch check | not run | It is a Part A check (RUNBOOK.md section 4). Part B has no extra catch check unless Asmit asks |

Independent check by the assistant (not an agent): a fresh clone of `part-b-run1` under /tmp (deleted afterwards):
`npm ci` ok, 16 test files / 133 tests pass, lint, typecheck and build exit 0, `npm run test:e2e` 1 passed. Same numbers as the tester.

## The timeline

| Time (IST) | What |
|---|---|
| 00:28 | Console helper made the 6 agents and the room; the template posted the kickoff |
| 00:31-00:38 | analyst questions answered, brief T5, `confirm brief`, plan of 22 steps, `approve 1-22` |
| 00:38-01:06 | executor: steps 1-21, test first, 42 commits |
| 01:06-02:15 | executor paused at step 22 (see slip 1) |
| 02:15-02:17 | port free, step 22 red then green, hand-off to the reviewer |
| 02:19-02:20 | reviewer CHANGES NEEDED, fix test first, APPROVE |
| 02:21-02:26 | tester 13 of 13 + Also PASS |
| 02:28 | advisor note |

## Two slips the advisor named, and my call

1. **A person was asked to act, then 69 minutes without a commit.** The executor asked a person to stop an `npm start`
   that held port 4321 (the browser test starts its own server there). The server was Asmit's own: `vite` started at 00:55
   from Terminal.app in the Sketchroom folder. The executor behaved right: it did not kill a process it had not started,
   wrote the test meanwhile, asked in the room twice (01:06, 01:37) and waited. Nobody answered in the room because the
   assistant was cut off from 00:53 to 02:01 by a Gateway restart. At 02:15 the assistant stopped the idle server (no
   browser was connected, Asmit had been away 14 minutes, no answer to a question in chat for 10 minutes); the executor
   went on by itself within seconds.
   **My call: not a workflow bug.** The cause was outside the template and no agent broke a rule. Asmit decides. Two fixes:
   a RUNBOOK rule (added: nobody starts the app on port 4321 during a run), or a separate port for the browser test (a SPEC
   change, for Part C).
2. **Test setup changed outside plan-T5.** `vite.config.ts` (`execArgv`, `39fa1de`) and `tests/setup.ts` (`d8c2902`, in a code
   commit). The executor named both in its hand-off and asked the reviewer to check them; the reviewer accepted both; no
   test was edited. **My call: not a bug.** Both depend on the Node and jsdom versions: watch them in Part C.

## Decided by Asmit (Thu 5 Nov 02:35, "do all of it") and done

1. **Merge:** done (see the top). Part C is cut from `start-c`.
2. **Zoom, pan, colour swatches, fill, stroke width go into Part C:** SPEC Part C item 13 (colour, fill, stroke width) and
   item 14 (zoom and pan). A note under Part B says run 1 left them out because no item asked for them (section 2.3).
3. **Browser test on its own port:** SPEC section 2.2 (from Part C on, port 4399), Part C item 15. Section 5 had the port
   4321 written into the Origin rule and the 409 message, which would have rejected the test on 4399: both now use the
   port the server listens on. The RUNBOOK rule "do not start `npm start` during a run" stays, because the Tester's own
   checks still use 4321.
4. **Push:** the commit with this file and the SPEC and RUNBOOK changes went to GitHub together with `6cc6060`.
5. **Node 20.19:** SPEC section 4 now says 20.19+, and Part C item 16 checks `package.json` `engines.node` and the README
   (today they say `>=20` and "Node 20 or newer": the Part C Executor changes them, test first).

Part C is bigger now: 16 items instead of 12. Part B took about 1 hour of work (2 hours with the 69-minute pause), so
expect Part C to take longer.

## Still open (small notes, no item asks for them)

- Reviewer note: a drag released outside the board stays stuck until the next click.
- Tester note: the board jumps up by the hint's height when the empty-state hint goes away.
- Next: Part C run 1. Parameters: prefix `tdd6`, room `sketchroom-c-run1`, Part `C`, Run `1`, Last task id `T5`. Recipe: RUNBOOK
  section 5. The template in Switch Console ("Sketchroom team v4") is unchanged, so no new import is needed.
