#!/bin/bash
# Runs the Antigravity (agy) discovery agent headless. Called by cron/launchd/Task Scheduler.
# Auth: uses the cached account login (run `agy` interactively once to sign in).
# File writes inside this folder are auto-allowed; python3 is allowed via
# permissions.allow in ~/.gemini/antigravity-cli/settings.json.
set -euo pipefail
cd "$(dirname "$0")"
# Use the venv so `python3` has gspread etc. (system python3 doesn't).
export PATH="$HOME/.local/bin:$PWD/.venv/bin:$PATH"
mkdir -p logs
TS=$(date +%Y%m%d-%H%M)
LOG="logs/gemini-$TS.log"
# Transient API errors (e.g. 503 high demand): retry the whole run.
for attempt in 1 2 3; do
  # Headless soft-denies any shell command not in permissions.allow, and the agent
  # improvises compound commands no rule list can predict, so approve all tools.
  # Guardrail is the prompt: append-only, sheet writes only via add_job.py.
  if agy -p "$(cat prompts/gemini.md)" --dangerously-skip-permissions --print-timeout 20m >> "$LOG" 2>&1; then
    break
  fi
  echo "agy attempt $attempt failed, see $LOG" >&2
  [ "$attempt" -lt 3 ] && sleep $((attempt * 120))
done
echo "agy run finished, log: $LOG"
