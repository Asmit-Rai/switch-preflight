#!/bin/sh
# Prepare the shared working folder of the four Preflight agents. Run it ONCE, before you use the
# Switch template, and again any time you want a brand-new sample project (it calls reset-demo.sh).
#
#   scripts/setup-demo.sh [FOLDER]        default FOLDER: ~/preflight-demo
#
# It makes:
#   FOLDER/kit/                   the checker (a clone of this repository)
#   FOLDER/.claude/settings.json  the permission rules the agents run under (see below)
#   FOLDER/rules/                 where the Scout puts the draft and the approved check list
#   FOLDER/project/               a sample project with 6 planted defects
#
# Why the permission file: Claude Code's auto mode refuses to run code that was just downloaded
# ("Code from External"), so the Inspector could not start the checker. The file allows exactly
# one command, kit/pf, and makes every risky git step (reset, rebase, push, rm, ...) ask a person.
# Agents must never write this file themselves: a person installs it.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
FOLDER=${1:-$HOME/preflight-demo}

mkdir -p "$FOLDER/rules" "$FOLDER/.claude"
FOLDER=$(cd "$FOLDER" && pwd)

if [ ! -d "$FOLDER/kit/.git" ]; then
  git clone --quiet "$HERE" "$FOLDER/kit"
  echo "setup-demo: checker cloned to $FOLDER/kit"
else
  git -C "$FOLDER/kit" pull --quiet --ff-only 2>/dev/null || true
  echo "setup-demo: checker already there ($FOLDER/kit)"
fi

if [ -f "$FOLDER/.claude/settings.json" ] && ! cmp -s "$FOLDER/.claude/settings.json" "$HERE/examples/claude-settings.json"; then
  cp "$FOLDER/.claude/settings.json" "$FOLDER/.claude/settings.json.bak"
  echo "setup-demo: old permission file kept as settings.json.bak"
fi
cp "$HERE/examples/claude-settings.json" "$FOLDER/.claude/settings.json"
echo "setup-demo: permission rules installed ($FOLDER/.claude/settings.json)"

sh "$HERE/scripts/reset-demo.sh" "$FOLDER"

echo
echo "setup-demo: done. Folder: $FOLDER"
echo "Next: in Switch Console, Templates > Import template > paste templates/preflight.template.yaml > Use."
echo "      Keep the default Shared working folder ($FOLDER)."
