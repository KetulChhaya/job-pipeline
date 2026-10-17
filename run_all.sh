#!/bin/bash
# Every lane, one command (alias: jobhunt). A failing lane doesn't stop the next one.
#   jobhunt                 Claude, Gemini, GitHub feeds, then a total + one notification
#   jobhunt feeds           only the named lanes, in the order given (claude, gemini, feeds)
#   jobhunt menu            numbered menu: pick lanes, a feeds dry run, or open the latest log
# Lanes switched off in config/settings.json (lanes.<name>.enabled) are skipped.
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
LANES="claude gemini feeds"
ENABLED="$(python3 pipeline/settings.py lanes)" || exit 1

run_lanes() {
  export JOB_NOTIFY=0 JOB_RUN_STARTED="$(date +%s)"
  local lane done=""
  for lane in "$@"; do
    case " $ENABLED " in
      *" $lane "*) ;;
      *) echo "skip $lane (disabled in config/settings.json)"; continue ;;
    esac
    "./run_$lane.sh" || true
    done="$done,$lane"
  done
  [ -z "$done" ] && return 0
  python3 pipeline/runner.py --total "${done#,}"
}

feeds_dry_run() {
  local copy
  copy="$(mktemp -t jobhunt-seen).db"
  cp pipeline/seen.db "$copy"
  JOB_NOTIFY=0 ./run_feeds.sh --dry-run --db "$copy"
}

open_latest_log() {
  local latest
  latest="$(ls -t logs/claude-*.log logs/gemini-*.log logs/feeds-*.log 2>/dev/null | head -1)"
  if [ -z "$latest" ]; then echo "No lane logs yet."; return 0; fi
  echo "Log: $PWD/$latest"
  ${JOBHUNT_PAGER:-less +G} "$latest"
}

menu() {
  cat <<'EOF'
━━ jobhunt ━━
  1) Claude   Greenhouse · Ashby · Lever
  2) Gemini   Workday · SmartRecruiters
  3) Feeds    GitHub lists (no LLM, ~3 min)
  4) All three
  5) Feeds dry run   (no sheet writes)
  6) Open the latest log
EOF
  local picks pick lanes=""
  read -r -p 'Pick one or more (e.g. "1 3"), Enter to quit: ' picks || picks=""
  [ -z "$picks" ] && return 0
  for pick in $picks; do
    case "$pick" in
      1) lanes="$lanes claude" ;;
      2) lanes="$lanes gemini" ;;
      3) lanes="$lanes feeds" ;;
      4) lanes="$ENABLED" ;;
      5) feeds_dry_run; return $? ;;
      6) open_latest_log; return $? ;;
      *) echo "pick 1-6, got: $pick" >&2; return 2 ;;
    esac
  done
  # shellcheck disable=SC2086  # word-splitting the lane list is intended
  run_lanes $lanes
}

case "${1:-}" in
  "") run_lanes $ENABLED ;;
  menu|-m) menu ;;
  *)
    for lane in "$@"; do
      case " $LANES " in *" $lane "*) ;; *) echo "unknown lane: $lane (use: $LANES, or menu)" >&2; exit 2 ;; esac
    done
    run_lanes "$@" ;;
esac
