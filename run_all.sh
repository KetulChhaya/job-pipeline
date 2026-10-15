#!/bin/bash
# Every lane, one command (alias: jobhunt): Claude, Gemini, GitHub feeds, then a total.
# A failing lane doesn't stop the next one. One macOS notification at the end.
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
export JOB_NOTIFY=0 JOB_RUN_STARTED="$(date +%s)"
./run_claude.sh || true
./run_gemini.sh || true
./run_feeds.sh || true
python3 pipeline/runner.py --total claude,gemini,feeds
