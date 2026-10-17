#!/bin/bash
# Gemini lane (Antigravity agy): Workday + SmartRecruiters. Live progress and a summary via
# pipeline/runner.py. Auth: the cached account login (run `agy` interactively once to sign in).
set -euo pipefail
cd "$(dirname "$0")"
# Use the venv so `python3` has gspread etc. (system python3 doesn't).
export PATH="$HOME/.local/bin:$PWD/.venv/bin:$PATH"
# Headless soft-denies any shell command not in permissions.allow, and the agent
# improvises compound commands no rule list can predict, so approve all tools.
# Guardrail is the prompt: append-only, sheet writes only via add_job.py.
# Transient API errors (e.g. 503 high demand): the runner retries the whole run.
# Model and retries come from config/settings.json (lanes.gemini.*).
MODEL="$(python3 pipeline/settings.py get lanes.gemini.model)"
RETRIES="$(python3 pipeline/settings.py get lanes.gemini.retries)"
WAIT="$(python3 pipeline/settings.py get lanes.gemini.retry_wait)"
exec python3 pipeline/runner.py --agent gemini --label "Workday · SmartRecruiters" \
  --stream lines --retries "$RETRIES" --retry-wait "$WAIT" -- \
  agy -p "$(cat prompts/gemini.md)" --model "$MODEL" \
  --dangerously-skip-permissions --print-timeout 20m
