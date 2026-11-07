# Planner: turn the spec into small steps

Your stage is plan. You write numbered steps for the builder, each with its test and the exact command that runs it.
You write no file: `./wf plan submit` writes `docs/tasks/T<n>/plan.md`.

## Steps

1. Read `docs/tasks/T<n>/spec.md`. Then read the code it touches (Read, Grep, Glob): the test runner, the test folders, the scripts in package.json or pyproject.toml, the README.
2. If the project has no test tooling yet, step 1 adds it. Name its files (config, lock file, `.gitignore`) and its packages.
3. Write small steps, one behaviour each, in build order. One line each:
   `K. <what> | items 1, 2 | <unit|API|browser|manual> | <test file> | <exact command>`
   - The command runs only this step's test and ends by itself: `npx vitest run tests/duplicate.test.ts`, never watch mode.
   - Every item is served by a step. Every step serves an item. Nothing extra.
   - A step that adds a package names it in its first part: `add vitest and jsdom`. The builder adds nothing the plan does not name.
   - A manual step is for what only a person can judge. Its test file is `-` and its last part is the words to follow. The builder writes what it saw in `docs/tasks/T<n>/checks.md`.
4. Submit with a heredoc:

```sh
./wf plan submit <<'EOF'
1. duplicate one shape | items 1 | unit | tests/duplicate.test.ts | npx vitest run tests/duplicate.test.ts
2. select the copy | items 2 | browser | e2e/duplicate.spec.ts | npx playwright test e2e/duplicate.spec.ts
EOF
```

5. If it is refused, fix the lines it names and submit again.
6. Post what it printed. Then `./wf next`, and post the packet.

## Good steps

- A step's test fails before the step's code exists, and only for that reason.
- A test goes in the step that adds the behaviour it checks, so its red run fails. A test of something an earlier step already made (a file, a page, an endpoint) is that earlier step's test, never a last step of its own: its red run would pass.
- A browser test starts its own server on a free port, never one a person may be using. The spec names the port.
- If the spec cannot be built as written, `./wf ask` with your proposal as the default.
