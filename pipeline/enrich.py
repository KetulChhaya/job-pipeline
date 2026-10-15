"""Description facts for filters.py: get the text (cache-first), then extract
YOE, stack terms and sponsorship wording. No keep/drop decisions here.
"""
import html
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta

import requests

from add_job import normalize_url
from fetchers import TIMEOUT, UA, _lever_text, html_to_text

DETAIL_SLEEP = 1  # seconds between detail requests (SmartRecruiters / Workday)

# ----------------------------------------------------------------------- YOE
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen".split())}
_NUM = (r"(?<![A-Za-z0-9])(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|"
        r"ten|eleven|twelve|thirteen|fourteen|fifteen)(?:\s*\(\s*\d{1,2}\s*\))?")  # "six (6)"
_Y = r"(?:years?|yrs?)(?![\s-]*(?:degree|college|university|program))"
_YOE_PATTERNS = [  # order matters: earlier matches are blanked out
    (re.compile(rf"{_NUM}\s*\+?\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*{_Y}", re.I), True),
    (re.compile(rf"(?:at least|minimum(?: of)?|min\.?)\s*{_NUM}\s*\+?\s*{_Y}", re.I), False),
    (re.compile(rf"{_NUM}\s*\+\s*{_Y}", re.I), False),
    (re.compile(rf"{_NUM}\s*(?:or more|plus)\s*{_Y}", re.I), False),
    (re.compile(rf"{_NUM}\s*{_Y}", re.I), False),
]
_PREF_HEAD = re.compile(r"\b(?:preferred|nice[ -]to[ -]haves?|bonus|desired|pluses)\b", re.I)
_REQ_HEAD = re.compile(
    r"\b(?:minimum|basic|required|requirements|qualifications|what you(?:'ll)? (?:need|bring)|"
    r"you have|about you|who you are|what we(?:'re)? looking for|responsibilities)\b", re.I)
_SKIP = re.compile(r"\b(?:preferred|bonus|nice[ -]to[ -]have|a plus|ideally|desired)\b", re.I)
_EXPERIENCE = re.compile(r"experience", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_CLAUSE = re.compile(r"[,;]|\s[–—-]\s")


def _num(s):
    s = s.lower()
    return int(s) if s.isdigit() else _WORDS.get(s)


def _requirement_sentences(text):
    """Clauses that state a requirement: their sentence mentions experience,
    they are not under a 'Preferred' heading, and the clause itself is not
    marked preferred/bonus ("6+ years ..., ideally full-stack" keeps the 6+)."""
    mode = "req"
    for raw in (text or "").replace("’", "'").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if len(line) <= 60 and not any(rx.search(line) for rx, _ in _YOE_PATTERNS):
            if _PREF_HEAD.search(line):
                mode = "pref"
                continue
            if _REQ_HEAD.search(line):
                mode = "req"
                continue
        if mode == "pref":
            continue
        for sent in _SENTENCE.split(line):
            if not _EXPERIENCE.search(sent):
                continue
            for clause in _CLAUSE.split(sent):
                if not _SKIP.search(clause):
                    yield clause


def extract_yoe(text):
    """(min, max) years required; max is None for 'N+'. (None, None) if not stated.
    With several numbers, the largest minimum wins."""
    best = None
    for sent in _requirement_sentences(text):
        work = sent
        for rx, is_range in _YOE_PATTERNS:
            for m in rx.finditer(work):
                lo = _num(m.group(1))
                hi = _num(m.group(2)) if is_range else None
                if lo is None or lo > 20:
                    continue
                if best is None or lo > best[0]:
                    best = (lo, hi)
            work = rx.sub(lambda m: " " * len(m.group(0)), work)
    return best if best else (None, None)


# ---------------------------------------------------------- sponsorship/stack
def extract_sponsorship(text, prof):
    """('clearance' | 'no' | 'yes' | 'unknown', matched phrase)."""
    t = " ".join((text or "").replace("’", "'").split())
    R = prof["_re"]
    for status, rx in (("clearance", R["clearance"]), ("no", R["sponsor_neg"]),
                       ("yes", R["sponsor_pos"])):
        m = rx.search(t)
        if m:
            return status, m.group(0)
    return "unknown", ""


def matched_groups(text, groups):
    """Names of the compiled groups that occur in text, in profile order."""
    t = " ".join((text or "").split())
    return [name for name, rx in groups.items() if rx.search(t)]


def extract_stack(text, prof, limit=6):
    """Dictionary terms found in text, the user's own stack first."""
    t = " ".join((text or "").split())
    found = [name for name, rx in prof["_re"]["dictionary"] if rx.search(t)]
    user = set(prof["stack"]["user_terms"])
    return ([n for n in found if n in user] + [n for n in found if n not in user])[:limit]


# ------------------------------------------------------------- descriptions
def init_cache(con):
    con.execute("""CREATE TABLE IF NOT EXISTS desc_cache(
        url_key TEXT PRIMARY KEY, description TEXT, country TEXT, fetched_at TEXT)""")


def prune_cache(con, days=3):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    con.execute("DELETE FROM desc_cache WHERE fetched_at < ?", (cutoff,))
    con.commit()


_ASHBY_BOARDS = {}  # board list url -> {job id: posting}, or None if it failed


def _ashby_detail(detail_url):
    """Ashby has no single-posting endpoint: fetch the board once per run."""
    board_url, job_id = detail_url.split("#", 1)
    if board_url not in _ASHBY_BOARDS:
        _ASHBY_BOARDS[board_url] = None
        r = requests.get(board_url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        _ASHBY_BOARDS[board_url] = {j.get("id"): j for j in r.json().get("jobs", [])}
        time.sleep(DETAIL_SLEEP)
    board = _ASHBY_BOARDS[board_url]
    if board is None or job_id not in board:
        raise LookupError(f"ashby posting {job_id} not available")
    j = board[job_id]
    country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or ""
    return j.get("descriptionPlain") or "", country


_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_H1B_FLAG = re.compile(r'"isH1bSponsor"\s*:\s*(true|false)')
_H1B_MARK = re.compile(r"^\[jobright\] h1b_sponsor=(yes|no)$", re.M)


def _jobright_detail(page):
    """(description, country) from a jobright.ai job page. The company's H-1B
    history is kept as a marker line that enrich_job reads (and the cache keeps)."""
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                text = html_to_text(item.get("description") or "")
                where = item.get("jobLocation") or {}
                where = where[0] if isinstance(where, list) and where else where
                country = ((where.get("address") or {}).get("addressCountry") or ""
                           if isinstance(where, dict) else "")
                flag = _H1B_FLAG.search(page)
                if text and flag:
                    text += f"\n[jobright] h1b_sponsor={'yes' if flag.group(1) == 'true' else 'no'}"
                return text, country
    return "", ""


def fetch_detail(job):
    """(description, country) from the job's detail_url. Raises on HTTP errors."""
    if job.get("ats") == "ashby":
        return _ashby_detail(job["detail_url"])
    r = requests.get(job["detail_url"], headers=UA, timeout=TIMEOUT)
    if job.get("ats") == "jobright":
        r.raise_for_status()
        time.sleep(DETAIL_SLEEP)
        return _jobright_detail(r.text)
    r.raise_for_status()
    d = r.json()
    time.sleep(DETAIL_SLEEP)
    if job.get("ats") == "smartrecruiters":
        sections = (d.get("jobAd") or {}).get("sections") or {}
        parts = []
        for key in ("jobDescription", "qualifications", "additionalInformation"):
            s = sections.get(key) or {}
            parts.append((s.get("title") or "") + "\n" + html_to_text(s.get("text") or ""))
        return "\n".join(parts).strip(), ""
    if job.get("ats") == "workday":
        info = d.get("jobPostingInfo") or {}
        return (html_to_text(info.get("jobDescription") or ""),
                (info.get("country") or {}).get("descriptor") or "")
    if job.get("ats") == "greenhouse":
        return html_to_text(d.get("content") or ""), ""
    if job.get("ats") == "lever":
        return _lever_text(d), d.get("country") or ""
    return "", ""


def enrich_job(job, prof, con=None, fetch=fetch_detail):
    """Fill description-derived fields in place and return the job.
    Feed jobs fall back to job["feed_sponsorship"] when the text is silent.
    Sets: description, country (if learned), yoe_min, yoe_max, sponsorship,
    sponsorship_phrase, user_groups, ai_groups, stack_terms, applied_ai_hit.
    Appends 'desc ?' to job['tags'] when no description could be had."""
    text = job.get("description") or ""
    if not text and job.get("detail_url"):
        key = normalize_url(job["job_link"])
        row = None
        if con is not None:
            row = con.execute("SELECT description, country FROM desc_cache WHERE url_key=?",
                              (key,)).fetchone()
        if row:
            text, country = row
        else:
            try:
                text, country = fetch(job)
            except Exception:
                text, country = "", ""
            if text and con is not None:
                try:  # commit at once: other writers (add_job) must not wait on network I/O
                    con.execute("INSERT OR REPLACE INTO desc_cache VALUES (?,?,?,?)",
                                (key, text, country, datetime.now().isoformat(timespec="seconds")))
                    con.commit()
                except sqlite3.Error:
                    pass  # the cache is an optimization; never fail a job over it
        if country and not job.get("country"):
            job["country"] = country
    job["description"] = text
    if not text:
        job.setdefault("tags", []).append("desc ?")
    both = f"{job.get('role', '')}\n{text}"
    R = prof["_re"]
    job["yoe_min"], job["yoe_max"] = extract_yoe(text)
    job["sponsorship"], job["sponsorship_phrase"] = extract_sponsorship(text, prof)
    mark = _H1B_MARK.search(text)
    if mark and mark.group(1) == "yes" and not job.get("feed_sponsorship"):
        job["feed_sponsorship"], job["feed_sponsorship_raw"] = "yes", "H-1B sponsor history"
    if job["sponsorship"] == "unknown" and job.get("feed_sponsorship"):
        # the description is silent: fall back to the feed's own field (Simplify)
        job["sponsorship"] = job["feed_sponsorship"]
        job["sponsorship_phrase"] = f"{job.get('feed', 'feed')}: {job.get('feed_sponsorship_raw', '')}"
    job["user_groups"] = matched_groups(both, R["user_groups"])
    job["ai_groups"] = matched_groups(both, R["ai_groups"])
    job["stack_terms"] = extract_stack(both, prof)
    job["applied_ai_hit"] = bool(R["ml_requires_any"].search(" ".join(text.split())))
    return job
