"""Per-person settings: config/settings.json laid over the built-in defaults.

Make yours with ./setup.sh, or copy config/settings.example.json.
Environment overrides: JOB_SETTINGS (another settings file), JOB_SHEET_ID,
GOOGLE_SERVICE_ACCOUNT.

Shell scripts read values through the CLI:
    python3 pipeline/settings.py get lanes.claude.model
    python3 pipeline/settings.py lanes        # enabled lanes, in run order
"""
import copy
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANE_ORDER = ["claude", "gemini", "feeds"]

DEFAULTS = {
    "sheet": {"id": "", "jobs_tab": "Pipeline Jobs", "runs_tab": "Pipeline Runs",
              "dedup_tabs": ["LinkedIn Easy Apply"]},
    "service_account": "service-account.json",
    "lanes": {
        "claude": {"enabled": True, "model": "claude-sonnet-5-5", "effort": "high"},
        "gemini": {"enabled": True, "model": "gemini-3.8-flash-medium",
                   "retries": 3, "retry_wait": 120},
        "feeds": {"enabled": True},
    },
    "fetch": {"hours": 30, "recent_title_days": 3, "timeout": 25,
              "board_sleep": 2, "detail_sleep": 1, "contact": ""},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def sheet_id(value):
    """Accept a full Google Sheets URL or a bare ID."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", value or "")
    return m.group(1) if m else (value or "").strip()


def path():
    return os.environ.get("JOB_SETTINGS") or os.path.join(ROOT, "config", "settings.json")


def load(p=None):
    p = p or path()
    data = {}
    if os.path.exists(p):
        try:
            with open(p) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(f"cannot read settings {p}: {e}") from e
    s = _merge(DEFAULTS, data)
    if os.environ.get("JOB_SHEET_ID"):
        s["sheet"]["id"] = os.environ["JOB_SHEET_ID"]
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT"):
        s["service_account"] = os.environ["GOOGLE_SERVICE_ACCOUNT"]
    s["sheet"]["id"] = sheet_id(s["sheet"]["id"])
    if not os.path.isabs(s["service_account"]):
        s["service_account"] = os.path.join(ROOT, s["service_account"])
    return s


def get(s, dotted):
    for key in dotted.split("."):
        s = s[key]
    return s


def _show(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v) if isinstance(v, (dict, list)) else str(v)


def main(argv):
    try:
        s = load()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if argv[:1] == ["lanes"] and len(argv) == 1:
        print(" ".join(l for l in LANE_ORDER if s["lanes"].get(l, {}).get("enabled")))
        return 0
    if argv[:1] == ["get"] and len(argv) == 2:
        try:
            print(_show(get(s, argv[1])))
        except (KeyError, TypeError):
            print(f"unknown setting: {argv[1]}", file=sys.stderr)
            return 1
        return 0
    print("usage: settings.py get <dotted.key> | settings.py lanes", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
