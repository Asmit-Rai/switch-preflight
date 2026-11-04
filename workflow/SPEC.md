# Sketchroom: build spec (v2)

Sketchroom is an original product. Do not copy code from Excalidraw or use its
name or logo.

This file sits in the project folder. Every agent works from it and from the
confirmed brief of the current task.

## 1. Two goals, in this order

1. **A reliable agent workflow**: analyst, planner, executor, reviewer, tester,
   with an advisor watching all of it. This is the main product.
2. **Sketchroom**, the app the workflow builds. It is the test subject.

If the two conflict, the workflow wins. We fix the workflow. We never hack the
app to get past a workflow problem.

## 2. How we build (rules for every agent)

### 2.1 Parts and runs
- The work is split into four parts, A to D (section 6). Do one part per run,
  in order. Do not start a part until the previous part passed.
- The Analyst gives every run its own task ID (T1, T2, ...). A rerun of a part
  gets a new task ID, and its brief names the part and the earlier run.
- Each run happens on its own git branch, `part-a-run1`, `part-a-run2`, and so
  on, always cut from the same starting commit. Only a passed run is merged,
  and a person does the merge.
- All agents use the same project folder (the repo root).

### 2.2 Test first
- Every "Done means" item names its check: **unit** (Vitest), **API** (Vitest
  against a real server on a random port), **browser** (Playwright), or
  **manual**. Manual is only for how something looks or reads.
- The Planner writes small steps. Each step names its test file and the exact
  command that runs it.
- The Executor, for each step: writes the test, runs it, and sees it **fail for
  the right reason** (red). Then writes the least code that passes, runs it and
  sees it **pass** (green), then runs the whole suite. It pastes the last lines
  of the red run and the green run into the thread.
- Nobody edits, skips or deletes a test to make it pass. A test change needs a
  plain reason in the thread and the Reviewer's approval.
- The Reviewer reads the test changes first. Tests must check behavior. They
  must not mock away the thing under test.
- The Tester starts from a clean install (`npm ci`), runs everything, and
  reports PASS or FAIL for **every** "Done means" item with the evidence. It
  writes a test for any item that has no automated check. It never takes the
  Executor's word for anything.
- From Part C on, browser tests (Playwright) start their own server on port
  4399 (`PORT=4399`), never on 4321. A person may have `npm start` running on
  4321, and a test run must not need it stopped.
- A FAIL goes back to the Executor with a short reason. After three loops on
  the same item, stop and ask a person.

### 2.3 Keep it simple
Build only what a "Done means" item needs. No extra files, features,
abstractions or dependencies. Only the dependencies in section 4. Take the
plain, obvious solution.

### 2.4 Workflow bugs and reruns
A **workflow bug** is any time an agent breaks its own rules or a hand-off
fails. Examples: acting before a person confirmed, skipping red then green,
editing outside the plan, handing off to the wrong agent or not at all,
stalling, not being able to use a tool it needs, or reporting PASS when it is
not true.

When one happens:
1. Stop the run.
2. Do not fix it by hand and do not edit app files yourself. A hand-fixed run
   does not count.
3. Add a row to `workflow/BUGS.md`:
   `| Run | Agent | What happened (quote it) | What should have happened | Fix | Rerun result |`
4. Fix the agent's template (instructions or settings), not the app.
5. Rerun the same part on a new branch from the same starting commit, in a
   fresh room.

A run is **clean** when people only did these four things: answered questions,
confirmed the brief, approved plan steps, merged. And every "Done means" item
of the part passes.

### 2.5 Part A is also the workflow check
A clean Part A run must show, each as a yes or no:
1. **Analyst** asked questions, got a confirmation, wrote the brief, handed off
   once, and changed no file.
2. **Planner** wrote numbered steps, each naming its test, and waited for
   approval of each step.
3. **Executor** showed red then green for every step, worked only on the run
   branch, and did nothing outside the plan.
4. **Reviewer** reviewed the tests first, then the code, against the brief, and
   gave a clear approve or changes-needed.
5. **Tester** ran from a clean install and reported PASS or FAIL per item with
   evidence.
6. **Advisor** posted a closing note: what went well, any rule that slipped,
   any risk, or "no issues".
7. Every hand-off woke the right agent on the first try.
8. The **catch check** below passed.

**Catch check.** After a clean Part A run, on a throwaway branch
`catch-check` cut from the passed run:
- (a) A person changes the page heading in the code (not the test) so it no
  longer says "Sketchroom". Ask `@tester` and `@reviewer` to check. Both must
  report it.
- (b) A person also edits the test to expect the wrong heading, so `npm test`
  passes. The Tester must still FAIL the item, and the Reviewer must flag the
  edited test.
Delete the branch afterwards. If either agent misses it, that is a workflow
bug.

## 3. What Sketchroom is

A hand-drawn style whiteboard in the browser, like Excalidraw, that **AI agents
in a Switch chat room can draw on**. Someone writes `@artist draw our login
flow` in the room and the diagram appears on the board, animated, with arrows
attached to the boxes. Agents can read the board and extend it. Everything a
person draws is saved in the browser only.

## 4. Constraints and stack

- **No database and no cookies.** Save with `localStorage` only.
- **No AI inside the app.** No LLM calls and no API keys in the browser. The AI
  is the Switch agent, which uses the API in section 5.
- **Runs offline.** No CDN, analytics or remote fonts.
- **One command.** `npm install && npm start` on Node 20.19+ serves the app at
  `http://127.0.0.1:4321`.
- **A tiny in-memory relay is allowed** (Part C), because a web page cannot
  receive messages from an agent on its own. It stores nothing between
  restarts.
- Only MIT, BSD, Apache or OFL licensed dependencies, listed in the README.

Stack (no others): React 18, TypeScript, Vite. The board is **SVG**, not
canvas. `roughjs` for the hand-drawn look. `zustand` for state. `zod` for
validating requests. `@dagrejs/dagre` for diagram layout (Part D). Express or
Fastify for the server. A bundled `@fontsource` handwriting font. Vitest,
`@playwright/test`, ESLint, `tsc --noEmit`.

Folders: `src/`, `server/`, `scripts/`, `examples/`, `docs/`, `workflow/`,
`e2e/`, `tests/`.

## 5. The drawing API (built in Parts C and D)

The server listens on `127.0.0.1` only. Port `4321`, overridable with `PORT`.
From Part C, `npm start` runs the server, which also serves the app (Vite in
middleware mode), so there is one process and one port.

| Endpoint | Purpose |
|---|---|
| `POST /api/draw` | An agent sends a drawing request. |
| `GET /api/scene` | An agent reads the board. `?format=outline` returns one plain-text line per element, for example `s_3 rect "Web app" at (120,80) 160x80 by human`. |
| `GET /api/events` | Server-sent events stream the browser listens to. |
| `POST /api/scene` | The browser sends a snapshot after changes (debounced 500 ms). The server keeps only the latest copy in memory. |
| `GET /api/health` | `{ok:true, boardOpen:true or false}` |

Rules:
- `POST` bodies must be `Content-Type: application/json` and at most 1 MB,
  otherwise 415 or 413.
- If an `Origin` header is present and is not `http://127.0.0.1:<port>` or
  `http://localhost:<port>`, return 403. This stops other websites drawing on
  the board. `<port>` is the port the server listens on: 4321, or the value of
  `PORT`.
- No browser has the board open: `POST /api/draw` returns **409** with
  `{ok:false, error:"No board is open. Open http://127.0.0.1:4321 first."}`
  (with the port the server listens on, 4321 unless `PORT` is set).
- Invalid request: **422** with `{ok:false, errors:[{path, message, hint}]}`.
  The `hint` says exactly how to fix it, because an agent reads it and retries.
  The board is unchanged.
- Success: `{ok:true, batchId, added, ids:{<yourId>:<realId>}}`.
- Limits: 300 elements per request, text up to 200 characters, labels up to 60.
- Text from requests is rendered as SVG text only, never as HTML.

### Request body

```
{
  "author": "artist",     // shown as a small tag on the board (max 40 chars)
  "mode": "append",       // "append" (default) or "replace" (clears the board)
  "animate": true,        // default true
  "title": "Login flow",  // optional text placed above the new content
  "diagram": { ... },     // optional (Part D), laid out automatically
  "shapes":  [ ... ]      // optional (Part C), absolute coordinates
}
```

At least one of `diagram` or `shapes` is required.

### `shapes`: raw shapes for pictures and anything free-form

Pixels at 100% zoom. Origin top-left, y grows downward. A normal visible area
is about 1200 by 800.

```json
{
  "author": "artist",
  "shapes": [
    {"type": "rect",     "x": 100, "y": 200, "w": 160, "h": 120, "color": "orange", "fill": "hachure"},
    {"type": "polyline", "points": [[90,200],[180,130],[270,200]], "closed": true, "color": "red", "fill": "solid"},
    {"type": "ellipse",  "x": 340, "y": 90, "w": 70, "h": 70, "color": "yellow", "fill": "solid"},
    {"type": "text",     "x": 110, "y": 350, "text": "My house", "fontSize": 24}
  ]
}
```

- `type`: `rect`, `ellipse`, `diamond`, `line`, `arrow`, `polyline`, `text`.
- Optional: `label` (text inside a shape), `fill` (`none`, `solid`, `hachure`),
  `strokeWidth` (1 to 6), `fontSize`, `id`. An `arrow` takes `points` or
  `from` and `to` shape ids.
- `color`: one of `black red orange yellow green blue purple gray` or
  `#rrggbb`. Anything else is a 422.

### `diagram`: nodes and edges, laid out automatically (Part D)

For flows, architectures and mind maps. The agent never works out coordinates.

```json
{
  "author": "artist",
  "mode": "append",
  "title": "Login flow",
  "diagram": {
    "direction": "LR",
    "nodes": [
      {"id": "user",  "label": "User",        "shape": "ellipse", "color": "gray"},
      {"id": "web",   "label": "Web app",     "shape": "rect",    "color": "blue"},
      {"id": "api",   "label": "Auth API",    "shape": "rect",    "color": "green"},
      {"id": "check", "label": "Valid?",      "shape": "diamond", "color": "yellow"},
      {"id": "db",    "label": "Users table", "shape": "rect",    "color": "purple"},
      {"id": "err",   "label": "Show error",  "shape": "rect",    "color": "red"}
    ],
    "edges": [
      {"from": "user",  "to": "web",   "label": "enters password"},
      {"from": "web",   "to": "api",   "label": "POST /login"},
      {"from": "api",   "to": "db",    "label": "lookup"},
      {"from": "api",   "to": "check"},
      {"from": "check", "to": "web",   "label": "yes: token"},
      {"from": "check", "to": "err",   "label": "no"}
    ]
  }
}
```

- `direction`: `LR` or `TB`. `shape`: `rect`, `ellipse` or `diamond`.
- An edge's `from` and `to` may be a node in this request **or the id of a
  shape already on the board** (from `GET /api/scene`).
- New content never overlaps existing shapes and never moves or changes shapes
  a person drew.
- Arrows made from edges stay attached to their nodes when a node moves.

### Command line helper `scripts/draw.mjs` (Part C)

- `node scripts/draw.mjs file.json` (or `&lt;file.json`) sends a request.
- `node scripts/draw.mjs --scene` prints the board as an outline.
- `node scripts/draw.mjs --clear` sends `{mode:"replace", shapes:[]}`.
- It prints the reply. For a 422 it prints each error with its hint in readable
  form and exits 1.

## 6. The four parts

Each "Done means" item ends with its check in brackets. Every part also needs
`npm test`, `npm run lint` and `npm run typecheck` to pass.

### Part A: scaffold (also the workflow check)

Build: a Vite, React and TypeScript project with Vitest and ESLint, a page
with the heading "Sketchroom" and an empty SVG board (`data-testid="board"`),
`workflow/BUGS.md` with the header row from section 2.4, a README, a
`.gitignore`.

Done means:
1. `npm install && npm start` serves a page at `http://127.0.0.1:4321` with the
   heading "Sketchroom" and an empty SVG board. [unit renders the app and finds
   both; the Tester also fetches the running page]
2. `npm test` passes and includes at least one test the Executor showed failing
   first. [red and green evidence in the thread]
3. `npm run lint` and `npm run typecheck` pass. [command output]
4. `.gitignore` excludes `node_modules` and build output, and `git status` is
   clean after a build. [command]
5. `workflow/BUGS.md` exists with the header row. [command]
6. The README has the run, test, lint and typecheck commands, and they work
   when followed from a clean clone. [manual: the Tester follows it]

### Part B: whiteboard core

Build: tools select, rectangle, diamond, ellipse, arrow, line, freehand, text.
Move, resize (corner handles), delete. Color (8 swatches), fill (none, solid,
hachure), stroke width. Zoom and pan. Undo and redo. Autosave. Export and
import. An empty-state hint. Hand-drawn style with a stored `seed` per element.

Done means:
1. Add, move, resize and delete work for every shape type. [unit, store]
2. Undo and redo restore the state exactly, for at least 100 steps. [unit]
3. Every element keeps a `seed`, and the same element renders the same path
   twice. [unit]
4. The board autosaves to `localStorage` key `sketchroom:scene:v1` (debounced
   300 ms) and restores on load. [unit, jsdom]
5. Corrupt saved data: the raw text is copied to `sketchroom:scene:backup`, an
   empty board starts, a notice shows, and nothing crashes. [unit]
6. A failed save (storage full) shows a warning and does not crash. [unit]
7. `document.cookie` is empty after use, and `package.json` has no database
   package. [unit]
8. Export JSON then import gives identical elements. [unit]
9. In a real browser, each of the seven drawing tools draws one shape with the
   mouse, a reload keeps them, and PNG export downloads a non-empty file.
   [browser: `e2e/smoke.spec.ts`]
10. The empty-state hint shows on an empty board and disappears after the first
    shape. [unit]

Left out of run 1: colour (8 swatches), fill, stroke width, zoom and pan are in
the Build line above, but no item asks for them, so run 1 did not build them
(section 2.3). They are Part C items 13 and 14.

### Part C: agents can draw shapes

Build: the server (section 5), the events stream, scene sync, `shapes`
requests, `scripts/draw.mjs`, the draw-in animation, the agent tag, the link
pill, and "Undo AI". Also what Part B left out: colour, fill, stroke width,
zoom and pan (items 13 and 14), a port of its own for browser tests (item 15)
and Node 20.19+ (item 16).

Done means:
1. `npm start` serves the app at port 4321 and listens on `127.0.0.1` only.
   [API]
2. `GET /api/health` is ok, and `boardOpen` is true only while a browser has
   the events stream open. [API]
3. A valid `shapes` request with a board open returns 200 with `batchId`,
   `added` and `ids`, and the shapes appear in the browser. [API + browser]
4. With no board open, `POST /api/draw` returns 409 with the message in
   section 5. [API]
5. A wrong content type returns 415, a body over 1 MB returns 413, and a
   foreign `Origin` returns 403 (also for `POST /api/scene`). [API]
6. An invalid request (unknown type, bad color, more than 300 elements) returns
   422 with `path`, `message` and `hint`, and the board is unchanged.
   [API + browser]
7. `GET /api/scene?format=outline` matches what is visible, including shapes a
   person drew. [browser]
8. One agent request is one undo step with Ctrl+Z, and "Undo AI" undoes only
   the latest agent request. [unit + browser]
9. The animation lasts at most 3 seconds however many shapes there are, and is
   skipped for `animate:false` and for reduced motion. [unit]
10. The agent tag shows the author. The link pill says "connected" while the
    events stream is open and "offline" when the server stops. [browser]
11. A label containing `<script>` or `<img onerror=...>` is shown as plain
    text. [browser]
12. `node scripts/draw.mjs examples/house.json` exits 0 and draws. A bad file
    prints readable hints and exits 1. [API]
13. Colour, fill and stroke width: the toolbar has 8 colour swatches (`black`,
    `red`, `orange`, `yellow`, `green`, `blue`, `purple`, `gray`), a fill choice
    (`none`, `solid`, `hachure`) and a stroke width (1 to 6). A change applies
    to the selected shape (one undo step) and to the next shape drawn. A shape
    keeps its colour, fill and width after a reload. [unit + browser]
14. Zoom and pan: zoom from 25% to 400% with Ctrl or Cmd and the wheel, and with
    buttons named `Zoom in`, `Zoom out` and `100%`. Pan by dragging the empty
    board while the space key is held. Zoom and pan never change a shape's
    stored coordinates: a shape drawn at 200% zoom is stored at half the screen
    distance, and agents always use coordinates at 100% zoom (section 5). Zoom
    and pan are not saved and not part of undo. [unit + browser]
15. Browser tests use port 4399 (`PORT=4399`): `e2e/smoke.spec.ts` and every
    later browser test pass while `npm start` is running on port 4321. The
    Origin rule of section 5 works on both ports. [browser: the Tester starts
    `npm start` first, then runs `npm run test:e2e`]
16. `package.json` has `engines.node` `>=20.19`, and the README says Node 20.19
    or newer. [unit]

### Part D: diagrams and docs

Build: `diagram` requests with dagre layout, attached arrows, extending an
existing board, `examples/`, and the docs.

Done means:
1. `examples/login-flow.json` makes 6 node boxes and 6 arrows, and no two node
   boxes overlap. [unit on layout + browser]
2. The same input always gives the same layout. [unit]
3. `direction` `LR` and `TB` give different orientations. [unit]
4. Moving a node moves the arrows attached to it. [unit + browser]
5. An edge that points at an existing shape's id adds the new node without
   moving or overlapping anything on the board. [unit]
6. An edge pointing at an unknown id returns 422 with a hint that names the
   missing id. [API]
7. `mode:"replace"` clears the board in one undoable step. [browser]
8. `examples/` holds `login-flow.json`, `house.json` and `mind-map.json`, and
   the server accepts every file in it. [API test that loops over the folder]
9. `docs/agent-guide.md` tells an agent to: check the board with `--scene`
   first; use `diagram` for flows and `shapes` for pictures; keep labels under
   30 characters and diagrams to 15 nodes or fewer; send one request at a
   time; read any 422 hints and retry once; end with one line in chat saying
   what was drawn; never use `replace` unless a person asked. [manual: the
   Tester follows it using only that file]
10. The README has a section "How an agent draws from a Switch room" and the
    license list. [manual]

## 7. Out of scope

Multiplayer and live cursors, accounts, cloud sync, any database, mobile touch
design, image upload, templates, dark mode, SVG export, eraser, in-app AI. Do
not add them, even if they look easy.
