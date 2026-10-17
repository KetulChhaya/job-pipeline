"""Driver: fetch fresh postings, filter them against config/profile.json,
write the kept and rejected jobs to runs/.

Usage:
    python discover.py --agent claude --sources greenhouse,ashby,lever
    python discover.py --agent gemini --sources workday,smartrecruiters
    python discover.py --agent feeds --sources github

Writes: runs/candidates_<agent>_<YYYYMMDD-HHMM>.json  (kept, best score first)
        runs/rejected_<agent>_<YYYYMMDD-HHMM>.json    (dropped, with reason)
Prints a JSON summary to stdout.
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from add_job import DB_PATH, _db, normalize_url  # noqa: E402
from enrich import enrich_job, fetch_detail, init_cache, prune_cache  # noqa: E402
from feeds import fetch_feed  # noqa: E402
from fetchers import FETCHERS, load_config, verify_board  # noqa: E402
from filters import (cap, company_key, full_check, load_profile,  # noqa: E402
                     merge, quick_check, score, title_key)
from progress import init as progress_init, progress  # noqa: E402

SOURCES = dict(FETCHERS, github=fetch_feed)
import settings  # noqa: E402

RECENT_TITLE_DAYS = settings.load()["fetch"]["recent_title_days"]
DEFAULT_HOURS = settings.load()["fetch"]["hours"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------- storage
def init_rejected(con):
    con.execute("""CREATE TABLE IF NOT EXISTS rejected(
        url_key TEXT PRIMARY KEY, company TEXT, role TEXT, reason TEXT,
        detail TEXT, agent TEXT, first_seen TEXT, last_seen TEXT)""")


def record_rejected(con, rejected, agent):
    now = datetime.now().isoformat(timespec="seconds")
    for r in rejected:
        con.execute(
            """INSERT INTO rejected VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(url_key) DO UPDATE SET reason=excluded.reason,
               detail=excluded.detail, agent=excluded.agent, last_seen=excluded.last_seen""",
            (normalize_url(r["job_link"]), r["company"], r["role"], r["reason"],
             r["detail"], agent, now, now))


def count_today(con, today=None):
    """{company_key: rows appended today} from the seen registry."""
    today = today or date.today().isoformat()
    counts = Counter()
    for (company,) in con.execute(
            "SELECT company FROM seen WHERE substr(first_seen, 1, 10) = ?", (today,)):
        counts[company_key(company)] += 1
    return dict(counts)


def _recent_titles(con, days=RECENT_TITLE_DAYS):
    """(company_key, title_key) of rows appended in the last `days` days, any
    lane: the same posting reached through a feed, jobright or a board link."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    return {(company_key(c), title_key(r)) for c, r in con.execute(
        "SELECT company, role FROM seen WHERE first_seen >= ?", (since,))}


def _skip_keys(con):
    """URLs already appended, or folded into another row by merge()."""
    keys = {r[0] for r in con.execute("SELECT url_key FROM seen")}
    try:
        keys |= {r[0] for r in con.execute(
            "SELECT url_key FROM rejected WHERE reason = 'merged'")}
    except sqlite3.Error as e:
        print(f"WARN: rejected log not readable: {e}", file=sys.stderr)
    return keys


# ---------------------------------------------------------------- filtering
def _rej(job, reason, detail):
    return {"company": job.get("company", ""), "role": job.get("role", ""),
            "location": job.get("location", ""), "job_link": job.get("job_link", ""),
            "source": job.get("source", ""), "reason": reason, "detail": detail or ""}


def format_yoe(lo, hi):
    if lo is None:
        return ""
    return f"{lo}–{hi}" if hi is not None else f"{lo}+"


def finalize(job):
    """Write the sheet-facing yoe / stack / notes fields."""
    job["yoe"] = format_yoe(job.get("yoe_min"), job.get("yoe_max"))
    job["stack"] = ", ".join(job.get("stack_terms", []))
    parts = [f"source: {job.get('source', '')}", f"score {job.get('score', 0)}",
             f"yoe {job['yoe']}" if job["yoe"] else "yoe ?",
             "sponsor ✓" if job.get("sponsorship") == "yes" else "sponsor ?"]
    parts += job.get("tags", [])
    if job.get("extra_note"):
        parts.append(job["extra_note"])
    job["notes"] = "; ".join(parts)
    return job


def run_filters(jobs, prof, con, agent, fetch=fetch_detail, today=None):
    """Returns (kept, rejected, skipped). kept is sorted best score first and
    finalized; rejected is a list of _rej dicts; skipped counts known URLs."""
    skip, recent = _skip_keys(con), _recent_titles(con)
    progress(f"filtering {len(jobs)} jobs")
    fetched = [0]

    def counted(j):
        text, country = fetch(j)
        fetched[0] += bool(text)  # successes only
        return text, country
    kept, rejected, skipped = [], [], 0
    for job in jobs:
        if (normalize_url(job.get("job_link")) in skip
                or (company_key(job.get("company")), title_key(job.get("role"))) in recent):
            skipped += 1
            continue
        c = quick_check(job, prof)
        if not c.keep:
            rejected.append(_rej(job, c.reason, c.detail))
            continue
        job["tags"] = list(c.tags)
        enrich_job(job, prof, con=con, fetch=counted)
        c = full_check(job, prof)
        if not c.keep:
            rejected.append(_rej(job, c.reason, c.detail))
            continue
        job["score"] = score(job, prof)
        kept.append(job)
    kept, folded = merge(kept)
    rejected += [_rej(j, "merged", link) for j, link in folded]
    kept, capped = cap(kept, prof, count_today(con, today))
    rejected += [_rej(j, "cap", detail) for j, detail in capped]
    try:
        record_rejected(con, rejected, agent)
        con.commit()
    except sqlite3.Error as e:  # the audit log must never block appends
        print(f"WARN: rejected log not written: {e}", file=sys.stderr)
    drops = Counter(r["reason"] for r in rejected).most_common()
    progress(f"{skipped} already appended · descriptions fetched: {fetched[0]}")
    progress(f"kept {len(kept)} · dropped "
             + (", ".join(f"{k} {n}" for k, n in drops) if drops else "none"))
    return [finalize(j) for j in kept], rejected, skipped


# --------------------------------------------------------------------- fetch
def _boards(cfg, src):
    boards = cfg.get(src, [])
    if src in ("workday", "github"):
        boards = [b for b in boards if isinstance(b, dict)]  # {base_url, board_path} / {name, ...}
    return boards


def fetch_all(cfg, sources, hours):
    jobs, dead, errors = [], [], []
    plan = [(src, _boards(cfg, src)) for src in sources if src in SOURCES]
    errors += [f"unknown source: {src}" for src in sources if src not in SOURCES]
    total = sum(len(b) for _, b in plan)
    progress(f"fetching {total} boards (" + ", ".join(f"{s} {len(b)}" for s, b in plan) + ")")
    i = 0
    for src, boards in plan:
        for key in boards:
            i += 1
            label = key if isinstance(key, str) else (key.get("board_path") or key.get("name"))
            tag = f"[{i:>2}/{total}] {src}/{label}"
            if verify_board(src, label) is False:
                dead.append(f"{src}/{label}")
                progress(f"{tag:<46} dead")
                continue
            try:
                if src == "workday":
                    got = SOURCES[src](key["base_url"], key["board_path"], hours=hours)
                else:
                    got = SOURCES[src](key, hours=hours)
            except Exception as e:  # per-board failure must not kill the run
                errors.append(f"{src}/{label}: {type(e).__name__}: {e}")
                progress(f"! {src}/{label}: {type(e).__name__}: {str(e)[:80]}")
                continue
            jobs.extend(got)
            progress(f"{tag:<46} {len(got):>3} fresh")
    return jobs, dead, errors


def _write(path, rows):
    with open(path, "w") as f:
        json.dump([{k: v for k, v in r.items() if k != "description"} for r in rows],
                  f, indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--sources", required=True,
                    help="comma-separated: greenhouse,lever,ashby,smartrecruiters,workday,github")
    ap.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                    help="lookback window; 30h gives overlap between 6h runs (dedup handles it)")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "boards.json"))
    ap.add_argument("--profile", default=os.path.join(ROOT, "config", "profile.json"))
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "runs"))
    args = ap.parse_args()
    progress_init(args.agent)

    try:
        prof = load_profile(args.profile)
        cfg = load_config(args.config)
    except (ValueError, OSError) as e:
        sys.exit(f"ERROR: {e}\nCopy config/profile.example.json and config/boards.example.json "
                 "(without .example), or run ./setup.sh.")
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    jobs, dead, errors = fetch_all(cfg, sources, args.hours)

    con = _db(args.db)
    init_cache(con)
    init_rejected(con)
    con.commit()
    try:
        prune_cache(con)
    except sqlite3.Error as e:  # another run holds the lock; pruning can wait
        print(f"WARN: desc_cache not pruned: {e}", file=sys.stderr)
    kept, rejected, skipped = run_filters(jobs, prof, con, args.agent)
    con.close()

    os.makedirs(args.out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    cand_path = os.path.join(args.out_dir, f"candidates_{args.agent}_{ts}.json")
    rej_path = os.path.join(args.out_dir, f"rejected_{args.agent}_{ts}.json")
    _write(cand_path, kept)
    _write(rej_path, rejected)

    print(json.dumps({
        "candidates_file": cand_path,
        "rejected_file": rej_path,
        "fetched": len(jobs),
        "already_known": skipped,
        "kept": len(kept),
        "dropped": dict(Counter(r["reason"] for r in rejected).most_common()),
        "dead_boards": dead,
        "errors": errors,
    }, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
