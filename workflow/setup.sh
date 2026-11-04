#!/bin/sh
# Make the Sketchroom folder that the six agents share, or put it back for a rerun.
#
#   workflow/setup.sh [FOLDER]                 make it (default: ~/hackthone/sketchroom)
#   workflow/setup.sh --reset [FOLDER] [TAG]   put it back at TAG (default: start-a) for a rerun
#
# Make: FOLDER becomes a git repo with one commit, SPEC.md, tagged start-a. Every run branch of
# Part A is cut from that tag. It also gets .claude/settings.json: the permission rules and the
# approval hook the agents run under. The permission file is hidden from git (.git/info/exclude),
# so `git status` stays clean and no agent can commit it. Agents never write it: a person installs it.
#
# Reset: for a rerun after a workflow bug. The folder goes back to TAG and every untracked or
# ignored file is deleted (node_modules, dist, ...). The branches of the failed run stay, as
# evidence. The permission file is installed again, so a fix in claude-settings.json is picked up.
# Run it yourself: it deletes files.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
RESET=no
if [ "${1:-}" = "--reset" ]; then
  RESET=yes
  shift
fi
FOLDER=${1:-$HOME/hackthone/sketchroom}

# The permission file, with the path of the approval hook filled in, hidden from git.
install_settings() {
  mkdir -p "$1/.claude" "$1/.git/info"
  sed "s|__HOOK__|$HERE/hooks/risky_git.py|" "$HERE/workflow/claude-settings.json" > "$1/.claude/settings.json"
  grep -qx '.claude/' "$1/.git/info/exclude" 2>/dev/null || printf '.claude/\n' >> "$1/.git/info/exclude"
}

if [ "$RESET" = yes ]; then
  TAG=${2:-start-a}
  [ -d "$FOLDER/.git" ] || { echo "setup: $FOLDER is not set up (run setup.sh without --reset first)" >&2; exit 1; }
  git -C "$FOLDER" rev-parse --verify -q "$TAG^{commit}" >/dev/null || { echo "setup: no such tag: $TAG" >&2; exit 1; }
  echo "setup: deleting these untracked files in $FOLDER:"
  git -C "$FOLDER" clean -ndx
  git -C "$FOLDER" switch -q --detach --discard-changes "$TAG"
  git -C "$FOLDER" clean -fdxq
  install_settings "$FOLDER"
  echo "setup: $FOLDER is back at $TAG ($(git -C "$FOLDER" rev-parse --short HEAD)). Branches kept:"
  git -C "$FOLDER" branch --list
  exit 0
fi

if [ -d "$FOLDER/.git" ]; then
  echo "setup: $FOLDER is already set up (use --reset to put it back at start-a)"
  exit 0
fi

mkdir -p "$FOLDER"
FOLDER=$(cd "$FOLDER" && pwd)
git -C "$FOLDER" init -q -b main
NAME=$(git -C "$HERE" config user.name 2>/dev/null || true)
EMAIL=$(git -C "$HERE" config user.email 2>/dev/null || true)
git -C "$FOLDER" config user.name "${NAME:-Sketchroom workflow}"
git -C "$FOLDER" config user.email "${EMAIL:-workflow@example.invalid}"

cp "$HERE/workflow/SPEC.md" "$FOLDER/SPEC.md"
git -C "$FOLDER" add SPEC.md
git -C "$FOLDER" -c commit.gpgsign=false commit -q -m "start: the spec"
git -C "$FOLDER" tag start-a

install_settings "$FOLDER"

echo "setup: done. $FOLDER holds SPEC.md at the commit tagged start-a."
echo "Next: start Switch Console and its local server, then import workflow/team.template.yaml (steps: workflow/RUNBOOK.md)."
