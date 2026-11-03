# Preflight check engine

Takes a project from "we think we're done" to "verified, ready to submit".

**The model proposes. Code decides.** Every required check is a plain function with tests.
Agents read the report, explain it and fix things. They never decide "ready": only
`python3 -m preflight verdict` can say that, for the current commit.

Python 3.11+, standard library only. Run everything from the repo root.

## Run it

```sh
# 1. run every check in a ruleset against a project (a git repo)
python3 -m preflight run --project DIR --rules rules/flip-the-switch.toml --format chat

# 2. the gate: is the last run READY for this exact commit, on a clean tree, and fresh?
python3 -m preflight verdict --project DIR

# 3. the submission pack (refuses while the gate is closed; same --max-age-min as verdict)
python3 -m preflight pack --project DIR --out SUBMISSION.md [--meta preflight.toml]

# 4. check a ruleset itself
python3 -m preflight lint --rules rules/flip-the-switch.toml

# tests (offline, deterministic)
python3 -m unittest discover -s tests -v
```

`run` options: `--json OUT` (also write the verdict JSON there), `--format table|chat|json`
(default `table`), `--offline` (every check that needs the network becomes SKIP), `--only ID,ID`
(run just these; **the other checks are recorded as SKIP, so a partial run can never be READY**),
`--no-write` (do not write `.preflight/verdict.json`).

Formats: `table` is the full report, every line at most 100 columns wide except the final verdict line.
`chat` fits one Discord message (at most 1800 characters; blocking checks first, then optional ones, then one
line for the passes; when it has to cut it adds a `(+N more, run with --format table)` line). `json` is the
verdict document. All three end with the line `VERDICT: READY` or `VERDICT: NOT READY (blocking: id, id)`
(`chat` shortens a very long list to `..., +N more`).

## Statuses and the verdict

| Status | Meaning |
|---|---|
| `PASS` | verified |
| `FAIL` | a required check failed |
| `WARN` | a non-required check failed (never blocks) |
| `SKIP` | could not be verified: offline, tool missing, rate limited, unreadable, not run. The detail says why |

**READY means every required check is PASS.** A required check that is SKIP blocks the verdict:
"cannot verify" is never "ready". A check that crashes becomes SKIP with the error, and the run goes on.

## Exit codes

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `run` | every required check passed | a required check failed or was SKIPped | no verdict could be produced: usage or config error (bad args, missing folder, invalid ruleset, unknown `--only` id) or an internal error (set `PREFLIGHT_DEBUG=1` for the traceback) |
| `verdict` | `GATE OK: ready at <sha>` | `GATE CLOSED: <one reason>` | usage error |
| `pack` | pack written | refused (gate closed, nothing written) | usage error (bad `--meta`) |
| `lint` | no ERROR lines | at least one ERROR line | usage error |

## The gate (`verdict`)

Reads `<project>/.preflight/verdict.json` and opens (prints `GATE OK: ready at <short sha>`, exit 0) only if
all of these hold, checked in this order. Otherwise it prints `GATE CLOSED: <reason>` and exits 1.

1. the file exists, parses, and matches schema 1
2. it agrees with itself: `verdict`, `blocking` and `summary` are **recomputed from `results`**, so a file
   edited to say READY while a result is FAIL is refused. A file with no required checks is refused
3. the verdict is READY, and `dirty` is false (it was produced on a clean tree)
4. `commit` equals the project's current `HEAD`
5. the working tree is clean (the `.preflight/` folder is ignored)
6. `created_at` is younger than `--max-age-min` (default 120) and not in the future

`pack` runs exactly this gate first and writes nothing when it is closed. Write the pack outside the repo or
under `.preflight/` (for example `--out DIR/.preflight/SUBMISSION.md`): a new file inside the repo would make
the tree dirty and close the gate again.

Limit, stated plainly: the gate proves the file is fresh, bound to this commit and self-consistent. It cannot
prove who wrote a fully consistent file. The backstops are the Captain's approval and re-running `run`.

## Rulesets (TOML)

```toml
[meta]
name = "Flip the Switch Hackathon"
version = 1
source = "https://www.wemakedevs.org/hackathons/switch/rules"
rubric = [["Use of Switch", 30], ["Technical execution", 25]]   # optional, advisory, parsed and kept
extends = "../rules/flip-the-switch.toml"                       # optional, see below

[[check]]
id = "license"                  # kebab-case, unique, at most 32 characters
title = "The repo has a LICENSE file"
type = "file_exists"            # one of the types below
required = true                 # default true; false means a failure is a WARN
fix_hint = "Add a LICENSE file."  # short text for the fixer agent
paths = ["LICENSE", "LICENSE.md"]   # then the parameters of the type
```

`extends` loads another ruleset first; a `[[check]]` here with the same `id` overrides only the keys it
sets, any other `[[check]]` is appended. `examples/sample.rules.toml` uses it to make `repo-public` advisory.
Paths in rulesets must be relative and stay inside the project.

`lint` reports one line per problem (`ERROR: ...` or `WARNING: ...`): unknown check type, duplicate or
badly formed id, missing required fields or parameters, unknown parameters, bad regex, bad timestamps,
bad `rubric`. It also prints a WARNING for every `command` check, because that runs code on this machine
and a person must approve it. `run` refuses a ruleset that has errors (exit 2).

## Check types

`*` marks a required parameter. Globs are case-insensitive: `*` stays inside one folder, `**` crosses folders.

| Type | Passes when | Parameters |
|---|---|---|
| `file_exists` | any of `paths` matches a tracked or untracked-but-not-ignored file | `paths*` (globs) |
| `file_absent` | no git-tracked file matches `paths`; a pattern without `/` matches at any depth | `paths*` (globs) |
| `readme_headings` | the README has a markdown heading containing each string (case-insensitive; `a\|b` accepts either) | `headings*`, `file` (`README.md`), `min_chars` (0: text under each heading) |
| `file_contains` | regex `pattern` is found in `file` | `file*`, `pattern*`, `flags` (letters `i` `m` `s`) |
| `text_absent` | regex `pattern` matches no tracked text file (binary and over 1 MB are skipped) | `pattern*`, `globs`, `flags` |
| `secret_scan` | no AWS key, GitHub/Slack/Discord token, PEM header, `sk-` key or `KEY/TOKEN/SECRET/PASSWORD = <random 20+ chars>` in tracked files, and with `history = true` in any commit | `history` (false) |
| `git_first_commit_after` | the earliest author date over `git log --all` is at or after `after` | `after*` (ISO 8601 with `Z` or offset) |
| `git_clean` | no uncommitted or untracked changes (`.preflight/` ignored) | none |
| `git_remote_public` | `origin` is on github.com and the GitHub API says `"private": false`. 404 fails; 403, 429, errors and `--offline` SKIP. Uses `GITHUB_TOKEN` if set (never printed) | none |
| `links_ok` | every http(s) link in `file` (and `extra_files`) answers 2xx/3xx in 10 s (HEAD, then GET). `localhost`, `example.com` and `.invalid` hosts fail as placeholders, even offline. Links in code are ignored. Sites that block scripts (401, 403, 429, 999) are SKIP, not dead | `file` (`README.md`), `extra_files`, `max_links` (40; more links than that is SKIP), `ignore` (URL regexes to skip) |
| `video_link_present` | the README links a video matching `pattern` | `file`, `pattern` (YouTube, Vimeo, Loom) |
| `video_max_seconds` | the first video is at most `max_seconds` long. Reads the YouTube watch page, or `local_file` with `ffprobe`. Anything unreadable is SKIP, never a guess | `max_seconds*`, `file`, `pattern`, `local_file` |
| `max_file_mb` | no git-tracked file is larger than `limit_mb` | `limit_mb*` |
| `command` | `cmd` exits 0 in the project (run through the shell). Detail shows the exit code and the last line | `cmd*`, `timeout` (120 s) |

### Accepting a secret finding

List accepted findings, one per line, in `.preflight-allow` at the project root (lines starting with `#` are comments):

```
docs/example.md:12      # path:line
tests/fixtures/*        # a path glob: any line
re:EXAMPLE_ONLY         # a regex matched against the source line
```

The report says how many findings were allowed. Findings are always masked: file, line, pattern name and
at most the first four characters of the value.

## verdict.json (schema 1)

```json
{
  "schema": 1,
  "ruleset": {"name": "...", "version": 1, "source": "..."},
  "project": "/abs/path",
  "commit": "<full sha or null>",
  "dirty": false,
  "created_at": "2026-11-04T09:12:00+05:30",
  "results": [
    {"id": "license", "title": "...", "type": "file_exists", "required": true,
     "status": "PASS", "detail": "found LICENSE", "hint": "..."}
  ],
  "summary": {"pass": 1, "fail": 0, "warn": 0, "skip": 0},
  "verdict": "READY",
  "blocking": []
}
```

`status` is PASS, FAIL, WARN or SKIP. `verdict` is READY or NOT_READY. `blocking` lists the ids of required
checks that are not PASS. `.preflight/` should be in the project's `.gitignore`.

## Add a check type

1. Write `def my_check(ctx: Context, params: Mapping[str, Any]) -> Outcome` in a module under `preflight/checks/`.
   Return `passed("...")`, `failed("...")` or `skipped("why it could not be verified")`. Keep the detail to one line.
   `ctx.project` is the folder, `ctx.offline` the flag, `ctx.fetch` the HTTP function (use it, so tests can fake it),
   `ctx.tracked_files()` the git file list. Raising is safe: the runner turns it into a SKIP.
2. Wrap it: `MY_CHECK = CheckType("my_check", my_check, {"file": Param("relpath", required=True)}, "one-line summary")`.
   Set `network=True` if it needs the internet, `runs_code=True` if it executes commands.
3. Add `MY_CHECK` to `CHECK_TYPES` in `preflight/checks/__init__.py`. Lint, `run` and the docs check pick it up from there.
4. Add a row to the table above (a test fails if a type is missing from this file) and tests in `tests/`:
   one pass, one fail, and one SKIP path. Tests must stay offline; use a temp git repo and a fake `fetch`.

Param kinds: `str`, `relpath`, `regex`, `globs`, `headings`, `relpaths`, `regexes`, `flags`, `bool`, `int`,
`number`, `timestamp`.

## Layout

```
preflight/  cli.py ruleset.py verdict.py report.py pack.py gitutil.py net.py markdown.py model.py
            checks/  one module per group of check types, registry in __init__.py
rules/      flip-the-switch.toml  oss-release.toml
examples/   make_sample.py  sample.rules.toml     (python3 examples/make_sample.py DEST [--fixed])
tests/      unittest, offline; fixtures/ holds small rulesets
```

### The sample project

`python3 examples/make_sample.py DEST` creates a repo with 6 planted defects: the README lacks the "Agents" and
"Reused" headings, no LICENSE, a committed `.env` with a fake token (built at runtime), a first commit dated
before the start, a dead `.invalid` link, no demo-video link. The `.env` trips two checks, so 7 required checks
are red. `--fixed` creates the all-green twin. Both have fixed author, dates and message, so they are
byte-for-byte reproducible. Their `origin` is a made-up GitHub URL that is never pushed, so use
`examples/sample.rules.toml` (the contest ruleset with `repo-public` made advisory) to reach READY.
