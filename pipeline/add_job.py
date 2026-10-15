"""THE ONLY WRITER. Agents never touch the Google Sheet directly; they call this.

Dedup is airtight by construction:
  1. At run start, the seen-set is rebuilt from BOTH job tabs
     ("Pipeline Jobs" and "LinkedIn Easy Apply") so the two writer lanes
     (laptop agents + Muse's LinkedIn lane) can never double-append.
  2. A local SQLite registry (seen.db) records every appended job.
  3. URL key: tracking params stripped, but ATS identifiers (gh_jid etc.) kept.
  4. Content key: normalized company|role|location catches the same job
     posted on two different boards with different URLs.

Usage:
    python add_job.py --agent claude --input candidates_claude_20261015-0035.json
    python add_job.py --agent gemini --input candidates.json --dry-run

Service-account auth: set GOOGLE_SERVICE_ACCOUNT to the JSON key path,
or place it at ./service-account.json next to this script's parent.
The sheet must be shared with the service account's client_email (Editor).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

SPREADSHEET_ID = os.environ.get("JOB_SHEET_ID", "1K4qWzt0ew0Dz4_PVn5iKKn3uf896Mhwzoi9uRqAJVoY")
JOBS_TAB = "Pipeline Jobs"
RUNS_TAB = "Pipeline Runs"
LINKEDIN_TAB = "LinkedIn Easy Apply"
HEADER = ["Date posted", "Company", "Role", "Location", "YOE",
          "Job link", "Stack summary", "Notes", "Status"]
RUNS_HEADER = ["Timestamp", "Agent", "Sources", "Fetched",
               "Appended", "Dup URL", "Dup content", "Errors", "Dropped"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "pipeline", "seen.db")

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "ref", "source",
}


def normalize_url(u):
    p = urlparse((u or "").strip())
    q = sorted((k, v) for k, v in parse_qsl(p.query)
               if k.lower() not in TRACKING_PARAMS)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "",
                       urlencode(q), ""))


def normalize_text(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def content_key(company, role, location):
    return "|".join([normalize_text(company), normalize_text(role),
                     normalize_text(location)])


# ------------------------------------------------------------------ sheet I/O
def _client(sa_path):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)


def _ensure_tab(sh, title, header):
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(header))
        ws.update("A1", [header])
        return ws
    if not ws.get("A1"):
        ws.update("A1", [header])
    return ws


def _ensure_runs_header(ws):
    """Older Pipeline Runs tabs have 8 columns; add the Dropped header."""
    n = len(RUNS_HEADER)
    if ws.col_count < n:
        ws.add_cols(n - ws.col_count)
    if ws.cell(1, n).value != RUNS_HEADER[-1]:
        ws.update_cell(1, n, RUNS_HEADER[-1])


def dropped_summary(input_path):
    """'level 39, family 18, ...' from the sibling rejected_*.json, '-' if none."""
    folder, name = os.path.split(input_path)
    if not name.startswith("candidates_"):
        return "-"
    path = os.path.join(folder, "rejected_" + name[len("candidates_"):])
    try:
        with open(path) as f:
            rejected = json.load(f)
    except (OSError, ValueError):
        return "-"
    counts = Counter(r.get("reason") or "?" for r in rejected)
    return ", ".join(f"{k} {v}" for k, v in counts.most_common()) or "none"


def load_seen_from_sheet(sh):
    """(seen_urls, seen_content) from both job tabs."""
    seen_urls, seen_content = set(), set()
    for tab in (JOBS_TAB, LINKEDIN_TAB):
        try:
            ws = sh.worksheet(tab)
        except Exception:
            continue
        rows = ws.get_all_values()[1:]  # skip header
        for r in rows:
            if len(r) < 6:
                continue
            company, role, loc, link = r[1], r[2], r[3], r[5]
            if link.strip():
                seen_urls.add(normalize_url(link))
            if company.strip() or role.strip():
                seen_content.add(content_key(company, role, loc))
    return seen_urls, seen_content


# ------------------------------------------------------------------- registry
def _db(db_path=None):
    db_path = db_path or DB_PATH
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS seen(
        url_key TEXT PRIMARY KEY, content_key TEXT UNIQUE,
        company TEXT, role TEXT, location TEXT, job_link TEXT,
        agent TEXT, source TEXT, first_seen TEXT)""")
    return con


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--input", required=True, help="candidates JSON from discover.py")
    ap.add_argument("--dry-run", action="store_true",
                    help="fully non-mutating: in-memory registry, no sheet writes")
    ap.add_argument("--db", default=None, help="override SQLite registry path (testing)")
    ap.add_argument("--skip-sheet", action="store_true",
                    help="offline test: skip all Google Sheets I/O, still persist registry")
    ap.add_argument("--service-account", default=None)
    args = ap.parse_args()

    with open(args.input) as f:
        candidates = json.load(f)
    # best first; older candidate files have no score and keep their order
    candidates.sort(key=lambda c: -(c.get("score") or 0))

    sa_path = (args.service_account
               or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
               or os.path.join(ROOT, "service-account.json"))

    seen_urls, seen_content = set(), set()
    sh = None
    use_sheet = not (args.dry_run or args.skip_sheet)
    if use_sheet:
        if not os.path.exists(sa_path):
            sys.exit(f"ERROR: service account key not found: {sa_path}\n"
                     f"Set GOOGLE_SERVICE_ACCOUNT or see README.md step 2.")
        sh = _client(sa_path).open_by_key(SPREADSHEET_ID)
        _ensure_tab(sh, JOBS_TAB, HEADER)
        seen_urls, seen_content = load_seen_from_sheet(sh)

    db_path = ":memory:" if args.dry_run else (args.db or DB_PATH)
    con = _db(db_path)
    for row in con.execute("SELECT url_key, content_key FROM seen"):
        seen_urls.add(row[0])
        seen_content.add(row[1])

    to_append, stats = [], {"dup_url": 0, "dup_content": 0, "errors": 0}
    now = datetime.now().isoformat(timespec="seconds")
    for c in candidates:
        try:
            link = (c.get("job_link") or "").strip()
            company = (c.get("company") or "").strip()
            role = (c.get("role") or "").strip()
            if not link or not company or not role:
                stats["errors"] += 1
                continue
            uk, ck = normalize_url(link), content_key(company, role, c.get("location"))
            if uk in seen_urls:
                stats["dup_url"] += 1
                continue
            if ck in seen_content:
                stats["dup_content"] += 1
                continue
            row = [c.get("date_posted", ""), company, role,
                   (c.get("location") or "").strip(), c.get("yoe", ""),
                   link, c.get("stack", ""), c.get("notes", ""), ""]
            to_append.append(row)
            seen_urls.add(uk)
            seen_content.add(ck)
            con.execute(
                "INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?,?,?,?,?)",
                (uk, ck, company, role, c.get("location", ""), link,
                 args.agent, (c.get("notes") or "").split(";")[0], now)
            )
        except Exception:
            stats["errors"] += 1
    con.commit()
    con.close()

    if to_append and use_sheet:
        sh.worksheet(JOBS_TAB).append_rows(to_append, value_input_option="RAW")

    summary = {
        "fetched": len(candidates),
        "appended": len(to_append),
        "dup_url": stats["dup_url"],
        "dup_content": stats["dup_content"],
        "errors": stats["errors"],
        "dry_run": args.dry_run,
    }

    if use_sheet:
        runs = _ensure_tab(sh, RUNS_TAB, RUNS_HEADER)
        _ensure_runs_header(runs)
        sources = sorted({(c.get("notes") or "").split(";")[0].replace("source:", "").strip()
                          for c in candidates if c.get("notes")})
        runs.append_row(
            [now, args.agent, ", ".join(sources) or "-",
             summary["fetched"], summary["appended"],
             summary["dup_url"], summary["dup_content"], summary["errors"],
             dropped_summary(args.input)],
            value_input_option="RAW")

    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
