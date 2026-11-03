#!/bin/sh
# Put the demo project back to its starting state, so the demo can be run again.
#
#   scripts/reset-demo.sh [FOLDER]
#
# FOLDER is the shared working folder of the four agents (default: ~/preflight-demo, else the
# folder Switch Console made for the agents under ~/.switch/agents/).
#
# It replaces FOLDER/project with a fresh sample submission that has 6 planted defects, and it
# keeps the old project in FOLDER/.old-projects/ instead of deleting it. It does NOT touch kit/,
# rules/ (the approved check list) or the room. Run it between rehearsals and before recording.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
FOLDER=${1:-}

if [ -z "$FOLDER" ] && [ -d "$HOME/preflight-demo/kit" ]; then
  FOLDER=$HOME/preflight-demo
fi
if [ -z "$FOLDER" ]; then
  for candidate in "$HOME"/.switch/agents/*/preflight; do
    if [ -d "$candidate/kit" ]; then FOLDER=$candidate; break; fi
  done
fi
if [ -z "$FOLDER" ] || [ ! -d "$FOLDER" ]; then
  echo "reset-demo: cannot find the agents' folder. Pass it as the first argument." >&2
  exit 2
fi

if [ -d "$FOLDER/project" ]; then
  STAMP=$(date +%Y%m%d-%H%M%S)
  mkdir -p "$FOLDER/.old-projects"
  mv "$FOLDER/project" "$FOLDER/.old-projects/project-$STAMP"
  echo "reset-demo: old project kept in $FOLDER/.old-projects/project-$STAMP"
fi

python3 "$HERE/examples/make_sample.py" "$FOLDER/project" >/dev/null
echo "reset-demo: fresh sample project at $FOLDER/project (6 planted defects)"
echo "reset-demo: now ask in the room:  @inspector run preflight"
