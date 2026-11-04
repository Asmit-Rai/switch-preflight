# Workflow bugs

One row each time an agent broke its own rules or a hand-off failed (SPEC.md section 2.4).
We fix the template, never the app, and run the part again. This is the real log: it lives
outside the Sketchroom folder, so a thrown-away run branch can never take its rows with it.

| Run | Agent | What happened (quote it) | What should have happened | Fix | Rerun result |
|---|---|---|---|---|---|
