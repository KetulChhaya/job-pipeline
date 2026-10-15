#!/bin/bash
# Claude lane: Greenhouse + Ashby + Lever. Live progress and a summary via pipeline/runner.py.
# Allow rules are passed as flags: .claude/settings.json is ignored in an untrusted workspace.
# The prompt goes in on stdin (runner --stdin) so the variadic --allowedTools can't swallow it.
set -euo pipefail
cd "$(dirname "$0")"
# Use the venv so `python3` has gspread etc. (system python3 doesn't).
export PATH="$PWD/.venv/bin:$PATH"
exec python3 pipeline/runner.py --agent claude --label "Greenhouse · Ashby · Lever" \
  --stdin prompts/claude.md --stream claude -- \
  claude -p --model claude-sonnet-5-5 --effort high \
  --verbose --output-format stream-json --permission-mode acceptEdits \
  --allowedTools "Bash(python3 pipeline/*)" "Bash(python3 -c:*)" "Read" "Edit(pipeline/fetchers.py)" "Edit(config/boards.json)"
