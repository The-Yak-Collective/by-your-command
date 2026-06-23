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

mkdir -p "$LOG_DIR"

# Redirect the rest of this script's output to the update log.
exec >>"$LOG_FILE" 2>&1
echo "=== update $(date -Is) ==="

# Update uv, but only if installed locally. Note that cron's PATH is minimal; make
# sure uv (and anything it needs) is reachable.
if [[ -x "$HOME/.local/bin/uv" ]]; then
  export PATH="$HOME/.local/bin:$PATH"
  uv self update --no-progress
fi

# Fast-forward only: refuse to create merge commits on the deploy box. If the local
# checkout has diverged from origin, this fails loudly instead of merging silently.
# Remember where we were first, so a failed verification can roll the deploy back.
cd "$REPO_DIR"
branch="$(git rev-parse --abbrev-ref HEAD)"
previous_head="$(git rev-parse HEAD)"
git pull --ff-only origin "$branch"

# Install any new or updated dependencies from the refreshed lockfile.
uv sync

# Verify the freshly-pulled code BEFORE touching the running bot. A bad commit or
# dependency bump must not be deployed automatically. We run the test suite and the
# linters; shell lint is skipped gracefully if shellcheck isn't on the deploy box.
# `|| verify_ok=false` keeps `set -e` from aborting before we can roll back.
verify_ok=true
echo "--- verifying: pytest"
uv run pytest -q || verify_ok=false
echo "--- verifying: ruff"
uv run ruff check || verify_ok=false
if command -v shellcheck >/dev/null 2>&1; then
  echo "--- verifying: shellcheck"
  shellcheck scripts/*.sh init.sh || verify_ok=false
else
  echo "--- verifying: shellcheck not installed, skipping shell lint"
fi

if [[ "$verify_ok" != true ]]; then
  # Roll the checkout back to the pre-pull commit and restore its dependencies, then
  # leave the already-running bot untouched. Better a slightly stale bot than a broken
  # one. The next nightly run will retry once the upstream problem is fixed.
  echo "verification FAILED; rolling back to $previous_head and leaving the bot running"
  git reset --hard "$previous_head"
  uv sync
  echo "=== update aborted $(date -Is) ==="
  exit 1
fi

# Verification passed — restart the bot so it picks up the new code and dependencies.
"$SCRIPT_DIR/bot.sh" restart

echo "=== update complete $(date -Is) ==="
