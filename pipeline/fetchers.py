"""ATS job-board fetchers. Every function returns a list of normalized job dicts:

    {"date_posted", "company", "role", "location", "yoe", "job_link", "stack",
     "notes", "source", "extra_note", "ats", "job_id", "country",
     "description", "detail_url"}

Fetchers only apply the freshness window. Role / location / YOE filtering
lives in filters.py, driven by config/profile.json.

All public, unauthenticated JSON APIs. Polite: small sleep between boards.
Run verify_board(source, key) before adding any new board token.
"""
import html
import json
import re
import time
from datetime import datetime, timezone, timedelta

import requests

import settings  # noqa: E402

_FETCH = settings.load()["fetch"]
UA = {"User-Agent": "job-pipeline/1.0 (personal job search; "
                    + (f"contact: {_FETCH['contact']})" if _FETCH["contact"] else "contact in repo README)")}
TIMEOUT = _FETCH["timeout"]
SLEEP_BETWEEN_BOARDS = _FETCH["board_sleep"]

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


def html_to_text(s):
    """HTML (possibly entity-escaped, as Greenhouse sends it) to plain text,
    one line per block element."""
    s = html.unescape(s or "")
    s = re.sub(r"(?i)<\s*/?\s*(?:br|p|li|ul|ol|div|h[1-6]|tr)\b[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v\xa0]+", " ", s)
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())


def _norm(date_posted, company, role, location, job_link, source, extra_note="",
          *, ats="", job_id="", country="", description="", detail_url=""):
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
        "source": source,
        "extra_note": extra_note,
        "ats": ats,
        "job_id": str(job_id or ""),
        "country": country or "",
        "description": description or "",
        "detail_url": detail_url or "",
    }


def _fresh(dt, hours):
    if not dt:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)


# ---------------------------------------------------------------- greenhouse
def fetch_greenhouse(board, hours=30):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                     params={"content": "true"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
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
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            j.get("company_name") or board,
            title, loc,
            j.get("absolute_url", ""),
            f"greenhouse/{board}",
            "" if verified else "date=updated_at, spot-check",
            ats="greenhouse", job_id=j.get("id"),
            description=html_to_text(j.get("content") or ""),
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# --------------------------------------------------------------------- lever
def _lever_text(j):
    parts = [j.get("descriptionPlain") or ""]
    for block in j.get("lists") or []:
        parts.append(block.get("text") or "")
        parts.append(html_to_text(block.get("content") or ""))
    parts.append(j.get("additionalPlain") or "")
    return "\n".join(p for p in parts if p).strip()


def fetch_lever(company, hours=30):
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
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            company, title, loc,
            j.get("hostedUrl") or j.get("applyUrl", ""),
            f"lever/{company}",
            ats="lever", job_id=j.get("id"), country=j.get("country") or "",
            description=_lever_text(j),
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# --------------------------------------------------------------------- ashby
def fetch_ashby(board, hours=30):
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
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            board, title, loc,
            j.get("jobUrl", ""),
            f"ashby/{board}",
            ats="ashby", job_id=j.get("id"),
            country=((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "",
            description=j.get("descriptionPlain") or "",
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# ------------------------------------------------------- smartrecruiters
def fetch_smartrecruiters(company_id, hours=30):
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
            loc_s = ", ".join(x for x in
                              [loc.get("city"), loc.get("region"), loc.get("country")]
                              if x)
            title = j.get("name", "")
            link = (j.get("applyUrl")
                    or f"https://jobs.smartrecruiters.com/{company_id}/{j.get('id', '')}")
            out.append(_norm(
                dt.strftime("%Y-%m-%d"), company_id, title, loc_s,
                link, f"smartrecruiters/{company_id}",
                ats="smartrecruiters", job_id=j.get("id"), country=loc.get("country") or "",
                detail_url=("https://api.smartrecruiters.com/v1/companies/"
                            f"{company_id}/postings/{j.get('id', '')}"),
            ))
        if len(items) < limit:
            break
        offset += limit
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# ------------------------------------------------------------------- workday
def fetch_workday(base_url, board_path, hours=30, limit=20):
    """base_url like https://nvidia.wd5.myworkdayjobs.com ; board_path like
    nvidia/NVIDIAExternalCareerSite. Endpoint shapes vary per tenant: verify first."""
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
            link = f"{base_url.rstrip('/')}/{board_name}{j.get('externalPath', '')}"
            out.append(_norm(
                dt.strftime("%Y-%m-%d"), company, title, loc,
                link, f"workday/{board_path}",
                ats="workday", job_id=j.get("externalPath", ""),
                detail_url=f"{base_url.rstrip('/')}/wday/cxs/{cxs_path}{j.get('externalPath', '')}",
            ))
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
