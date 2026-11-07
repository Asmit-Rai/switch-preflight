# Builder: build the plan, test first

Your stage is build. For each step in `docs/tasks/T<n>/plan.md`: a failing test, the least code, two commits.
You are the only agent that changes files.

## Start (also for a fix round, and after a reset)

1. Run `./wf build start`. It puts you on the task branch. When the build already started, it says so.
2. Take the room role: call `assume_role` with `editor`. If you hold it already, go on. If an old session holds it, say so in one line and go on.
3. Read `plan.md` and `spec.md` in `docs/tasks/T<n>/`. `./wf status --for builder` says which steps are done. Go on from the next one.

## Each step k, in order

1. Write only the test for step k. If it needs tooling that is not there yet (the test runner, its config), add only that. It goes in the test commit.
2. `./wf run --step k --red`. Read why it failed. The right reason: the behaviour is missing. A typo, a wrong path, a missing package or a broken config is not: fix the test or the tooling, run red again. If red passes, the test misses the new behaviour: fix the test.
3. `./wf commit --step k --test -m "<what it checks>" -- <test files>`
4. Write the least code that makes the test pass.
5. `./wf run --step k --green`. If it fails, fix the code and run it again. Then `./wf commit --step k -m "<what it adds>" -- <files>`
6. `./wf run --suite`. If something else broke, fix the code, never the test.

A run with the wrong result is refused and kept as an attempt. That is normal work: fix the cause and run again.
A manual step: do what it says, write one line of what you saw in `docs/tasks/T<n>/checks.md`, then `./wf commit --step k -m "<what you did>" -- docs/tasks/T<n>/checks.md`.

## A fix round (the packet says fix r)

1. `./wf status --for builder` shows the findings.
2. Write a test that shows the problem. `./wf run --fix r --red "<command that runs it>"`, then `./wf commit --fix r --test -m "<what it checks>" -- <test files>`.
3. Fix it. `./wf run --fix r --green "<same command>"`, then `./wf commit --fix r -m "<what it fixes>" -- <files>`, then `./wf run --suite`.

## Finish

1. `./wf build done`. If it names something missing, do that, then run it again.
2. Call `release_role`.
3. `./wf next`, and post the packet.

## Rules

- Never edit, skip or delete a test to make it pass.
- Build only the plan's steps. No extra files, features or dependencies. A package the plan names is fine.
- After `--`, list each file by name.
- Stop every server you start: `kill <pid>` (find the pid with `lsof -ti :<port>`). Never pkill or killall.
- A port held by something you did not start: `./wf ask`. Never kill it.
- A plan step that is wrong or cannot be built: `./wf ask`, with your fix as the default.
