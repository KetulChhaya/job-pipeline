#!/bin/bash
# First-time setup (safe to re-run). Asks for your sheet, key and lanes, writes
# config/settings.json, copies the example profile/boards if you have none,
# adds the `jobhunt` alias, and checks the sheet is reachable (read-only).
# Never overwrites an existing config/profile.json or config/boards.json.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$PWD"

ask() {  # ask "Question" "default"  -> answer on stdout
  local ans
  read -r -p "$1${2:+ [$2]}: " ans || true
  printf '%s' "${ans:-$2}"
}
has() { command -v "$1" >/dev/null 2>&1 || [ -x "$HOME/.local/bin/$1" ]; }

echo "━━ job-pipeline setup ━━"

# 1. Python + venv
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null \
  || { echo "Python 3.10+ is required (python3 on PATH is older or missing)." >&2; exit 1; }
if [ "${SETUP_SKIP_VENV:-}" != "1" ]; then
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  echo "✓ .venv ready"
fi

# 2. Questions (Enter keeps the value in brackets)
current() {  # current settings value (sheet | key), if any
  [ -f config/settings.json ] || return 0
  python3 -c "import json,sys; s=json.load(open('config/settings.json'));
print(s.get('sheet', {}).get('id', '') if sys.argv[1] == 'sheet' else s.get('service_account', ''))" "$1" \
    2>/dev/null || true
}
SHEET="$(ask "Google Sheet URL (share it with your service account as Editor)" "$(current sheet)")"
[ -n "$SHEET" ] || { echo "a Google Sheet URL is required" >&2; exit 1; }
KEY="$(ask "Service-account JSON key path" "$(current key)")"
KEY="${KEY:-service-account.json}"

lane() {  # lane VAR Label cli   -> sets VAR to y / n
  local var=$1 label=$2 cli=$3
  if [ -n "$cli" ] && ! has "$cli"; then
    echo "  $cli ($label lane) not found — lane off. Install it and re-run setup to turn it on."
    printf -v "$var" n
    return
  fi
  case "$(ask "Enable the $label lane? (y/n)" y)" in [Nn]*) printf -v "$var" n ;; *) printf -v "$var" y ;; esac
}
lane CL Claude claude
lane GE Gemini agy
lane FE feeds ""

# 3. config/settings.json (merged into any existing one)
SHEET="$SHEET" KEY="$KEY" CL="$CL" GE="$GE" FE="$FE" python3 - <<'PY'
import json, os
p = "config/settings.json"
s = json.load(open(p if os.path.exists(p) else "config/settings.example.json"))
s.pop("_comment", None)
s.setdefault("sheet", {})["id"] = os.environ["SHEET"]
s["service_account"] = os.environ["KEY"]
for lane, var in (("claude", "CL"), ("gemini", "GE"), ("feeds", "FE")):
    s.setdefault("lanes", {}).setdefault(lane, {})["enabled"] = os.environ[var] == "y"
json.dump(s, open(p, "w"), indent=1)
PY
echo "✓ config/settings.json"

# 4. Your profile and boards (copied once, then yours to edit)
for f in profile boards; do
  if [ ! -f "config/$f.json" ]; then cp "config/$f.example.json" "config/$f.json"; echo "✓ config/$f.json (from the example)"; fi
done

# 5. jobhunt alias
case "$(basename "${SHELL:-bash}")" in zsh) RC="$HOME/.zshrc" ;; *) RC="$HOME/.bashrc" ;; esac
LINE="alias jobhunt=\"$HERE/run_all.sh\""
if ! grep -qs '^alias jobhunt=' "$RC"; then echo "$LINE" >> "$RC"; echo "✓ alias jobhunt added to $RC"; fi

# 6. Read-only sheet check
if [ "${SETUP_SKIP_CHECK:-}" != "1" ]; then
  .venv/bin/python pipeline/add_job.py --agent setup --check \
    || echo "! Sheet check failed: share the sheet with the service-account email above (Editor) and re-run."
fi

cat <<EOF

Next:
  1. edit config/profile.json — max_yoe, needs_sponsorship, family.keep, stack.user_groups / user_terms
  2. open a new terminal (or: source $RC)
  3. jobhunt menu   → pick 5 for a safe dry run
EOF
