"""ATS job-board fetchers. Every function returns a list of normalized job dicts:

    {"date_posted", "company", "role", "location", "yoe",
     "job_link", "stack", "notes"}

All public, unauthenticated JSON APIs. Polite: small sleep between boards.
Run verify_board(source, key) before adding any new board token.
"""
import json
import re
import time
from datetime import datetime, timezone, timedelta

import requests

UA = {"User-Agent": "job-pipeline/1.0 (personal job search; contact in repo README)"}
TIMEOUT = 25
SLEEP_BETWEEN_BOARDS = 2

US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
    "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
    "vt", "va", "wa", "wv", "wi", "wy", "dc",
]


def load_config(path="config/boards.json"):
    with open(path) as f:
        return json.load(f)


def _parse_dt(value):
    """Parse ISO-8601-ish strings or epoch seconds/ms. Returns aware datetime or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:  # ms
            v /= 1000
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    # relative like "3 days ago" / "Posted 2 Days Ago"
    m = re.search(r"(\d+)\s+(day|hour|minute|week)", s, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"day": timedelta(days=n), "hour": timedelta(hours=n),
                 "minute": timedelta(minutes=n), "week": timedelta(weeks=n)}[unit]
        return datetime.now(timezone.utc) - delta
    if s.lower() in ("today", "just posted", "posted today"):
        return datetime.now(timezone.utc)
    if s.lower() in ("yesterday", "posted yesterday"):
        return datetime.now(timezone.utc) - timedelta(days=1)
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def matches_role(title, keywords):
    t = (title or "").lower()
    return any(k in t for k in keywords)


NON_US = [
    "brazil", "brasil", "são paulo", "sao paulo", "canada", "toronto",
    "mexico", "india", "bangalore", "bengaluru", "hyderabad", "united kingdom",
    "uk", "london", "ireland", "dublin", "germany", "berlin", "france",
    "paris", "spain", "madrid", "netherlands", "amsterdam", "poland",
    "warsaw", "singapore", "japan", "tokyo", "australia", "sydney",
    "latam", "emea", "apac",
]


def _has_us(loc):
    return any(re.search(r"\b" + re.escape(st) + r"\b", loc) for st in US_STATES)


def matches_location(loc, keywords):
    loc = (loc or "").lower()
    if not loc:
        return False
    if any(re.search(r"\b" + re.escape(c) + r"\b", loc) for c in NON_US):
        # "São Paulo, Remote" is not a US job; "San Francisco, Remote" is.
        return _has_us(loc)
    if any(k in loc for k in keywords):
        return True
    return _has_us(loc)


def _norm(date_posted, company, role, location, job_link, source, extra_note=""):
    notes = f"source: {source}"
    if extra_note:
        notes += f"; {extra_note}"
    return {
        "date_posted": date_posted,
        "company": company or "",
        "role": role or "",
        "location": location or "",
        "yoe": "",
        "job_link": job_link or "",
        "stack": "",
        "notes": notes,
    }


def _fresh(dt, hours):
    if not dt:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)


# ---------------------------------------------------------------- greenhouse
def fetch_greenhouse(board, hours=30, cfg=None):
    cfg = cfg or {}
    tk, lk = cfg.get("title_keywords", []), cfg.get("location_keywords", [])
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    cutoff_note = []
    out = []
    for j in r.json().get("jobs", []):
        pub_raw = j.get("first_published")
        dt = _parse_dt(pub_raw)
        verified = True
        if not dt:
            dt = _parse_dt(j.get("updated_at"))
            verified = False
        if not _fresh(dt, hours):
            continue
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        if not matches_role(title, tk) or not matches_location(loc, lk):
            continue
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            j.get("company_name") or board,
            title, loc,
            j.get("absolute_url", ""),
            f"greenhouse/{board}",
            "" if verified else "date=updated_at, spot-check",
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# --------------------------------------------------------------------- lever
def fetch_lever(company, hours=30, cfg=None):
    cfg = cfg or {}
    tk, lk = cfg.get("title_keywords", []), cfg.get("location_keywords", [])
    r = requests.get(f"https://api.lever.co/v0/postings/{company}",
                     params={"mode": "json"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):  # {"ok": false, ...} means bad slug
        raise ValueError(f"lever slug '{company}' invalid: {data}")
    out = []
    for j in data:
        dt = _parse_dt(j.get("createdAt"))
        if not _fresh(dt, hours):
            continue
        cats = j.get("categories") or {}
        title = j.get("text", "")
        loc = cats.get("location", "")
        if not matches_role(title, tk) or not matches_location(loc, lk):
            continue
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            company, title, loc,
            j.get("hostedUrl") or j.get("applyUrl", ""),
            f"lever/{company}",
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# --------------------------------------------------------------------- ashby
def fetch_ashby(board, hours=30, cfg=None):
    cfg = cfg or {}
    tk, lk = cfg.get("title_keywords", []), cfg.get("location_keywords", [])
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if not j.get("isListed", True):
            continue
        dt = _parse_dt(j.get("publishedAt"))
        if not _fresh(dt, hours):
            continue
        title = j.get("title", "")
        loc = j.get("location") or ""
        if j.get("isRemote"):
            loc = (loc + ", Remote").strip(", ")
        if not matches_role(title, tk) or not matches_location(loc, lk):
            continue
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            board, title, loc,
            j.get("jobUrl", ""),
            f"ashby/{board}",
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# ------------------------------------------------------- smartrecruiters
def fetch_smartrecruiters(company_id, hours=30, cfg=None):
    cfg = cfg or {}
    tk, lk = cfg.get("title_keywords", []), cfg.get("location_keywords", [])
    out, offset, limit = [], 0, 100
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    while True:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings",
            params={"limit": limit, "offset": offset}, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data.get("content", [])
        if not items:
            break
        for j in items:
            dt = _parse_dt(j.get("releasedDate"))
            if not _fresh(dt, hours):
                # postings are newest-first; stop early once clearly old
                if dt and dt < cutoff - timedelta(days=2):
                    return out
                continue
            loc = j.get("location") or {}
            country = (loc.get("country") or "").lower()
            if country and country != "us":
                continue
            loc_s = ", ".join(x for x in
                              [loc.get("city"), loc.get("region"), loc.get("country")]
                              if x)
            title = j.get("name", "")
            if not matches_role(title, tk) or not matches_location(loc_s, lk):
                continue
            link = (j.get("applyUrl")
                    or f"https://jobs.smartrecruiters.com/{company_id}/{j.get('id', '')}")
            out.append(_norm(dt.strftime("%Y-%m-%d"), company_id, title, loc_s,
                             link, f"smartrecruiters/{company_id}"))
        if len(items) < limit:
            break
        offset += limit
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# ------------------------------------------------------------------- workday
def fetch_workday(base_url, board_path, hours=30, cfg=None, limit=20):
    """base_url like https://nvidia.wd5.myworkdayjobs.com ; board_path like
    nvidia/NVIDIAExternalCareerSite. Endpoint shapes vary per tenant: verify first."""
    cfg = cfg or {}
    tk, lk = cfg.get("title_keywords", []), cfg.get("location_keywords", [])
    cxs_path = board_path.strip("/") if "/" in board_path else f"{board_path}/{board_path}"
    board_name = board_path.strip("/").split("/")[-1]
    company = board_path.strip("/").split("/")[0]
    url = f"{base_url.rstrip('/')}/wday/cxs/{cxs_path}/jobs"
    out, offset = [], 0
    while True:
        r = requests.post(url, json={"searchText": "", "appliedFacets": {},
                                     "limit": limit, "offset": offset},
                          headers={**UA, "Content-Type": "application/json"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data.get("jobPostings", [])
        if not items:
            break
        for j in items:
            dt = _parse_dt(j.get("postedOn") or j.get("startDate"))
            if not _fresh(dt, hours):
                continue
            title = j.get("title", "")
            loc = j.get("locationsText") or (j.get("bulletFields", [""])[0] if j.get("bulletFields") else "")
            ext_parts = j.get("externalPath", "").strip("/").split("/")
            if len(ext_parts) >= 2 and ext_parts[0] == "job":
                primary_loc = ext_parts[1].replace("-", " ")
                if not loc or "location" in loc.lower():
                    loc = f"{primary_loc} ({loc})" if loc else primary_loc
                elif primary_loc.lower() not in loc.lower():
                    loc = f"{loc}, {primary_loc}"
            if not matches_role(title, tk) or not matches_location(loc, lk):
                continue
            link = f"{base_url.rstrip('/')}/{board_name}{j.get('externalPath', '')}"
            out.append(_norm(dt.strftime("%Y-%m-%d"), company, title, loc,
                             link, f"workday/{board_path}"))
        if len(items) < limit or offset >= 200:
            break
        offset += limit
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
}


def verify_board(source, key):
    """True if the board token returns a non-empty live job list."""
    try:
        if source == "greenhouse":
            d = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{key}/jobs",
                             headers=UA, timeout=TIMEOUT).json()
            return bool(d.get("jobs"))
        if source == "ashby":
            d = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{key}",
                             headers=UA, timeout=TIMEOUT).json()
            return bool(d.get("jobs"))
        if source == "lever":
            d = requests.get(f"https://api.lever.co/v0/postings/{key}",
                             params={"mode": "json", "limit": 1},
                             headers=UA, timeout=TIMEOUT).json()
            return isinstance(d, list) and bool(d)
        if source == "smartrecruiters":
            d = requests.get(f"https://api.smartrecruiters.com/v1/companies/{key}/postings",
                             params={"limit": 1}, headers=UA, timeout=TIMEOUT).json()
            return d.get("totalFound", 0) > 0
        return None  # workday shapes vary; verify manually per tenant
    except Exception:
        return False
