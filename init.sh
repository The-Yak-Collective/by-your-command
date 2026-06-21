#!/usr/bin/env bash
#
# One-time (and safely re-runnable) setup for the by-your-command Discord bot.
#
# This script is idempotent: re-running it refreshes dependencies but never
# overwrites your .env, and never adds duplicate cron entries. It will:
#
#   1. ensure `uv` is installed,
#   2. install dependencies into a local .venv (uv sync),
#   3. create .env from .env.example if you don't have one yet,
#   4. install cron jobs to start the bot on boot and refresh it nightly,
#   5. start the bot.
#
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# --- 1. Ensure uv is installed --------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
	echo "uv not found; installing via the official installer..."
	# If you'd rather not pipe a script to sh, install uv another way first
	# (e.g. `pip install --user uv`) and re-run this script.
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$HOME/.local/bin:$PATH"
fi
echo "using uv: $(command -v uv)"

# --- 2. Install dependencies ----------------------------------------------------
uv sync

# --- 3. Create .env if absent (never overwrite existing configuration) ----------
if [ -f .env ]; then
	echo ".env already exists; leaving it untouched"
else
	cp .env.example .env
	echo "created .env from .env.example — edit it and add your DISCORD_BOT_TOKEN"
fi

# --- 4. Install cron jobs (idempotently) ----------------------------------------
# A marker comment tags the lines we manage. We strip any previously-managed lines
# before re-adding them, so running init.sh repeatedly never creates duplicates.
MARKER="# by-your-command (managed)"
BOT_SH="$REPO_DIR/scripts/bot.sh"
UPDATE_SH="$REPO_DIR/scripts/update.sh"

new_crontab="$(
	# Keep existing entries, minus any we previously managed.
	crontab -l 2>/dev/null | grep -vF "$MARKER" || true
	# Start the bot on boot.
	echo "@reboot $BOT_SH start $MARKER"
	# Refresh code + dependencies and restart, nightly at 03:00.
	echo "0 3 * * * $UPDATE_SH $MARKER"
)"
echo "$new_crontab" | crontab -
echo "installed cron jobs (@reboot start; nightly update at 03:00)"

# --- 5. Start the bot (only once it is actually configured) ---------------------
# Starting before a token is set would just crash-loop, so we auto-start only when
# DISCORD_BOT_TOKEN has a value. The @reboot cron job starts it after the next boot
# regardless, and you can always start it by hand once .env is filled in.
if grep -qE '^DISCORD_BOT_TOKEN=.+$' .env; then
	"$BOT_SH" start
else
	echo "DISCORD_BOT_TOKEN is not set in .env yet — not starting the bot."
	echo "Edit .env, then run: scripts/bot.sh start"
fi

echo "done."
