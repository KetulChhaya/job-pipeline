"""GitHub-hosted job lists as a source (config/boards.json "github").

fetch_feed(entry, hours) returns the same job dicts as fetchers.py, plus
"feed" (the entry name) and "feed_sponsorship" ("yes" | "no" | "").
Formats: simplify (JSON listings), speedyapply and jobright (Markdown tables).
"""
import json
import re
from datetime import datetime, timedelta, timezone

import requests

from atslinks import resolve
from fetchers import TIMEOUT, UA, _norm
from progress import progress

_SIMPLIFY_SPONSOR = {"Offers Sponsorship": "yes", "Does Not Offer Sponsorship": "no",
                     "U.S. Citizenship is Required": "no"}
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)
_HREF = re.compile(r'href="([^"]+)"')
_AGE = re.compile(r"^(\d+)\s*([mhdw])$")
_MD_LINK = re.compile(r"\[(.+?)\]\((https?://[^)\s]+)\)")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(cell):
    return " ".join(_TAG.sub(" ", _BR.sub("; ", cell)).replace("*", "").split())


def parse_simplify(text, now):
    rows = []
    for j in json.loads(text):
        if not (j.get("active") and j.get("is_visible", True) and j.get("date_posted")):
            continue
        rows.append({"company": j.get("company_name") or "", "role": j.get("title") or "",
                     "location": "; ".join(j.get("locations") or []), "link": j.get("url") or "",
                     "posted": datetime.fromtimestamp(j["date_posted"], tz=timezone.utc),
                     "sponsorship": j.get("sponsorship") or ""})
    return rows, 0


def parse_speedyapply(text, now):
    """| Company | Position | Location | [Salary |] Posting | Age |  (age like '1d');
    the 'Other' table has no Salary column."""
    rows, skipped = [], 0
    for line in text.splitlines():
        if not line.startswith("|") or "href" not in line:
            continue
        cells = _cells(line)
        ok = len(cells) in (5, 6)
        link = _HREF.search(cells[-2]) if ok else None
        age = _AGE.match(cells[-1]) if ok else None
        if not (link and age):
            skipped += 1
            continue
        delta = timedelta(**{_UNITS[age.group(2)]: int(age.group(1))})
        rows.append({"company": _plain(cells[0]), "role": _plain(cells[1]),
                     "location": _plain(cells[2]), "link": link.group(1),
                     "posted": now - delta, "sponsorship": ""})
    return rows, skipped


def _month_day(s, now):
    """'Oct 14' -> end of that day (UTC); a date in the future means last year."""
    try:
        d = datetime.strptime(f"{s.strip()} {now.year}", "%b %d %Y")
    except ValueError:
        return None
    d = d.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    if d - now > timedelta(days=1):
        d = d.replace(year=now.year - 1)
    return d


def parse_jobright(text, now):
    """| Company | Job Title | Location | Work Model | Date Posted |  ('↳' = company above)"""
    rows, skipped, company = [], 0, ""
    for line in text.splitlines():
        if not line.startswith("|") or "](" not in line:
            continue
        cells = _cells(line)
        if len(cells) != 5:
            skipped += 1
            continue
        if cells[0] != "↳":
            m = _MD_LINK.search(cells[0])
            company = (m.group(1) if m else _plain(cells[0])).replace("*", "").strip()
        title = _MD_LINK.search(cells[1])
        posted = _month_day(cells[4], now)
        if not (title and company and posted):
            skipped += 1
            continue
        rows.append({"company": company, "role": title.group(1).replace("*", "").strip(),
                     "location": _plain(cells[2]), "link": title.group(2),
                     "posted": posted, "sponsorship": ""})
    return rows, skipped


PARSERS = {"simplify": parse_simplify, "speedyapply": parse_speedyapply,
           "jobright": parse_jobright}


def fetch_feed(entry, hours=30, now=None, get=None):
    """Fresh rows of one feed as job dicts. Raises on HTTP or format errors."""
    parse = PARSERS[entry["format"]]
    now = now or datetime.now(timezone.utc)
    r = (get or requests.get)(entry["url"], headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    rows, skipped = parse(r.text, now)
    if skipped:
        progress(f"  github/{entry['name']}: skipped {skipped} rows that didn't parse")
    cutoff = now - timedelta(hours=hours)
    out = []
    for row in rows:
        if row["posted"] < cutoff or not row["link"]:
            continue
        ats, detail_url = resolve(row["link"])
        job = _norm(row["posted"].strftime("%Y-%m-%d"), row["company"], row["role"],
                    row["location"], row["link"], f"github/{entry['name']}",
                    ats=ats or "", detail_url=detail_url)
        job["feed"] = entry["name"]
        job["feed_sponsorship"] = _SIMPLIFY_SPONSOR.get(row["sponsorship"], "")
        job["feed_sponsorship_raw"] = row["sponsorship"] if job["feed_sponsorship"] else ""
        out.append(job)
    return out
