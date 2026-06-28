#!/bin/bash
# Fidukat — daily single-shot evaluation for the BNB Hack Track 1 competition.
# Runs ONE --once cycle (not a loop). Scheduled 2x/day via cron as a safety margin:
# the 1st run forces the daily keepalive trade; the 2nd run is a backup that only
# trades if the 1st somehow failed (trades_today still 0 for the current UTC day).
#
# NOTE on macOS: cron must have Full Disk Access, otherwise TCC silently blocks
# reads/writes under ~/Documents (where this project lives) and the run dies before
# it can log anything. So we FIRST drop a heartbeat into $HOME (a non-TCC-protected
# path) — if that line appears but state/cron.log doesn't, cron fired but lacks Full
# Disk Access. Grant it in System Settings ▸ Privacy & Security ▸ Full Disk Access ▸ +
# ▸ /usr/sbin/cron. We do NOT use `set -e` so a blocked/failed step is logged, not
# silent.
set -uo pipefail

HEARTBEAT="$HOME/.fidukat_cron.log"
echo "fired $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC (pid $$)" >> "$HEARTBEAT" 2>/dev/null || true

PROJ="/Users/liliekandriani/Documents/CodingWorkspace/fidukat-trading-bot"
LOG="$PROJ/state/cron.log"

# cron has a minimal PATH — add npm-global (twak CLI) + common bins.
export PATH="/Users/liliekandriani/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

if ! cd "$PROJ"; then
    echo "$(date -u '+%FT%TZ') ! cd $PROJ failed (TCC/Full Disk Access? path moved?)" >> "$HEARTBEAT"
    exit 1
fi

mkdir -p "$PROJ/state"
echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC | daily --once =====" >> "$LOG"

# load secrets/config from .env
set -a
# shellcheck disable=SC1091
if ! source "$PROJ/.env"; then
    echo "  ! source .env failed (TCC/Full Disk Access?)" >> "$LOG"
    exit 1
fi
set +a

"$PROJ/.venv/bin/python" loop/agent.py --once >> "$LOG" 2>&1
echo "----- exit=$? -----" >> "$LOG"
