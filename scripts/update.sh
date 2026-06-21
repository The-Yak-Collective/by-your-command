#!/usr/bin/env bash
#
# Nightly deployment refresh for the by-your-command bot.
#
# Pulls the latest code from origin, updates dependencies, and restarts the bot.
# Intended to be run from cron (installed by init.sh). All output is appended to the
# update log under the bot's state directory.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/by-your-command"
LOG_DIR="$STATE_DIR/logs"
LOG_FILE="$LOG_DIR/update.log"

# cron's PATH is minimal; make sure uv (and anything it needs) is reachable.
export PATH="$HOME/.local/bin:$PATH"

mkdir -p "$LOG_DIR"

# Redirect the rest of this script's output to the update log.
exec >>"$LOG_FILE" 2>&1
echo "=== update $(date -Is) ==="

cd "$REPO_DIR"

# Fast-forward only: refuse to create merge commits on the deploy box. If the local
# checkout has diverged from origin, this fails loudly instead of merging silently.
branch="$(git rev-parse --abbrev-ref HEAD)"
git pull --ff-only origin "$branch"

# Install any new or updated dependencies from the refreshed lockfile.
uv sync

# Restart the bot so it picks up the new code and dependencies.
"$SCRIPT_DIR/bot.sh" restart

echo "=== update complete $(date -Is) ==="
