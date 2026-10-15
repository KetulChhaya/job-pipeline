#!/bin/bash
# GitHub feeds lane (SimplifyJobs, speedyapply, jobright): plain discover + add_job, no LLM.
# Extra flags pass through to the runner, e.g. ./run_feeds.sh --dry-run --db /tmp/seen-copy.db
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
exec python3 pipeline/runner.py --agent feeds --label "GitHub feeds" --direct github "$@"
