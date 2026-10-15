#!/bin/bash
# Runs the Claude discovery agent headless. Called by cron/launchd/Task Scheduler.
set -euo pipefail
cd "$(dirname "$0")"
# Use the venv so `python3` has gspread etc. (system python3 doesn't).
export PATH="$PWD/.venv/bin:$PATH"
mkdir -p logs
TS=$(date +%Y%m%d-%H%M)
# Allow rules are passed as flags: .claude/settings.json is ignored in an untrusted workspace.
# Prompt goes via stdin so the variadic --allowedTools can't swallow it.
claude -p \
  --permission-mode acceptEdits \
  --allowedTools "Bash(python3 pipeline/*)" "Bash(python3 -c:*)" "Read" "Edit(pipeline/fetchers.py)" "Edit(config/boards.json)" \
  < prompts/claude.md >> "logs/claude-$TS.log" 2>&1
echo "claude run finished, log: logs/claude-$TS.log"
