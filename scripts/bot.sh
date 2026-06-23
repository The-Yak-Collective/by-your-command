#!/usr/bin/env bash
#
# Process control for the by-your-command Discord bot.
#
# Usage: bot.sh {start|stop|restart|status}
#
# The bot runs as a detached `uv run python -m bot` process. We track it with a PID
# file under the XDG state directory — the same root bot.state uses — so the PID
# file, logs, and per-command state all live together, outside the git checkout.
#
set -euo pipefail

# --- Locate the repo root (this script lives in <repo>/scripts/) ----------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

# --- State root: MUST match bot.state.base_dir() in Python ----------------------
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/by-your-command"
PID_FILE="$STATE_DIR/bot.pid"
LOG_DIR="$STATE_DIR/logs"
LOG_FILE="$LOG_DIR/bot.log"

# cron runs with a minimal PATH; the uv installer drops uv in ~/.local/bin. Make
# sure we can find it however this script was invoked.
if [[ -x "$HOME/.local/bin/uv" ]]; then
	export PATH="$HOME/.local/bin:$PATH"
fi

# Echo the PID of the running bot and return 0, or return 1 if it is not running.
# A PID file whose process is gone (e.g. after a reboot) counts as "not running".
running_pid() {
	[ -f "$PID_FILE" ] || return 1
	local pid
	pid="$(<"$PID_FILE")"
	# kill -0 checks for the process's existence without actually signalling it.
	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		echo "$pid"
		return 0
	fi
	return 1
}

start() {
	local pid
	if pid="$(running_pid)"; then
		echo "already running (pid $pid)"
		return 0
	fi
	mkdir -p "$STATE_DIR" "$LOG_DIR"
	cd "$REPO_DIR"
	# nohup + & detaches the bot so it outlives this shell (and the cron job).
	nohup uv run python -m bot >>"$LOG_FILE" 2>&1 &
	local newpid=$!
	echo "$newpid" >"$PID_FILE"
	echo "started (pid $newpid); logging to $LOG_FILE"
}

stop() {
	local pid
	if ! pid="$(running_pid)"; then
		echo "not running"
		rm -f "$PID_FILE"
		return 0
	fi
	kill "$pid"
	# Give it a few seconds to shut down cleanly before giving up.
	for _ in 1 2 3 4 5; do
		kill -0 "$pid" 2>/dev/null || break
		sleep 1
	done
	rm -f "$PID_FILE"
	echo "stopped (pid $pid)"
}

status() {
	local pid
	if pid="$(running_pid)"; then
		echo "running (pid $pid)"
	else
		echo "not running"
		return 1
	fi
}

case "${1:-}" in
start) start ;;
stop) stop ;;
restart)
	stop
	start
	;;
status) status ;;
*)
	echo "usage: $0 {start|stop|restart|status}" >&2
	exit 2
	;;
esac
