import json
from datetime import datetime, timedelta, timezone

import pytest

from feeds import fetch_feed, parse_jobright, parse_simplify, parse_speedyapply

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
TS = int(NOW.timestamp())

SIMPLIFY = json.dumps([
    {"company_name": "WPP", "title": "Software Engineer", "locations": ["New York, NY", "Remote in USA"],
     "url": "https://job-boards.greenhouse.io/wpp/jobs/8887554002", "date_posted": TS - 3600,
     "active": True, "is_visible": True, "sponsorship": "Offers Sponsorship"},
    {"company_name": "Plus", "title": "Backend Engineer", "locations": ["Santa Clara, CA"],
     "url": "https://jobs.lever.co/plus-2/49900d1b-1074-4372-82b0-a5f33c038a8c/apply",
     "date_posted": TS - 7200, "active": True, "is_visible": True,
     "sponsorship": "U.S. Citizenship is Required"},
    {"company_name": "Closed", "title": "Software Engineer", "locations": ["Austin, TX"],
     "url": "https://x/closed", "date_posted": TS, "active": False, "is_visible": True},
    {"company_name": "Hidden", "title": "Software Engineer", "locations": ["Austin, TX"],
     "url": "https://x/hidden", "date_posted": TS, "active": True, "is_visible": False},
    {"company_name": "Old", "title": "Software Engineer", "locations": ["Austin, TX"],
     "url": "https://x/old", "date_posted": TS - 3 * 86400, "active": True, "is_visible": True},
])

SPEEDY = """# New Grad USA
| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.nvidia.com"><strong>NVIDIA</strong></a> | Software Engineer - New College Grad 2027 | Santa Clara, CA | $172k/yr | <a href="https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite/job/US-CA-Santa-Clara/SWE_JR1"><img src="x" alt="Apply" width="70"/></a> | 0d |
| <a href="https://acme.com"><strong>Acme</strong></a> | Backend Engineer | Austin, TX<br>Remote | $150k/yr | <a href="https://jobs.lever.co/acme/49900d1b-1074-4372-82b0-a5f33c038a8c"><img src="x"/></a> | 1d |
| <a href="https://old.com"><strong>Old</strong></a> | Software Engineer | Austin, TX | $1 | <a href="https://x/old"><img src="x"/></a> | 2d |
| <a href="https://bad.com"><strong>Bad</strong></a> | Software Engineer | Austin, TX | <a href="https://x/bad">x</a> |
| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://afs.com"><strong>Accenture Federal Services</strong></a> | Software Engineer | Chantilly, VA | <a href="https://boards.greenhouse.io/afs/jobs/4721696006"><img src="x"/></a> | 0d |
"""

JOBRIGHT = """| Company | Job Title | Location | Work Model | Date Posted |
| ----- | --------- |  --------- | ---- | ------- |
| **[General Motors](https://www.gm.com)** | **[Software Engineer AV HIL (Early Career))](https://jobright.ai/jobs/info/aaa?utm_source=1103)** | Sunnyvale, CA, United States | Hybrid | Oct 15 |
| ↳ | **[Software Engineer, Platform](https://jobright.ai/jobs/info/bbb)** | Austin, TX, United States | On Site | Oct 14 |
| **[Northwood](https://northwood.io)** | **[Software Engineer – General](https://jobright.ai/jobs/info/ccc)** | Torrance, CA, United States | On Site | Oct 12 |
| ↳ | **[Software Engineer II](https://jobright.ai/jobs/info/ddd)** | Torrance, CA | On Site | someday |
"""


def test_parse_simplify_skips_inactive_and_hidden():
    rows, skipped = parse_simplify(SIMPLIFY, NOW)
    assert [r["company"] for r in rows] == ["WPP", "Plus", "Old"]
    assert rows[0]["location"] == "New York, NY; Remote in USA"
    assert rows[0]["sponsorship"] == "Offers Sponsorship"
    assert skipped == 0


def test_parse_speedyapply_rows_and_ages():
    rows, skipped = parse_speedyapply(SPEEDY, NOW)
    assert [(r["company"], r["role"]) for r in rows] == [
        ("NVIDIA", "Software Engineer - New College Grad 2027"), ("Acme", "Backend Engineer"),
        ("Old", "Software Engineer"), ("Accenture Federal Services", "Software Engineer")]
    assert rows[1]["location"] == "Austin, TX; Remote"
    assert rows[1]["posted"] == NOW - timedelta(days=1)
    assert skipped == 1


def test_parse_jobright_continuation_rows_and_dates():
    rows, skipped = parse_jobright(JOBRIGHT, NOW)
    assert [(r["company"], r["role"]) for r in rows] == [
        ("General Motors", "Software Engineer AV HIL (Early Career))"),
        ("General Motors", "Software Engineer, Platform"),
        ("Northwood", "Software Engineer – General")]
    assert rows[0]["link"] == "https://jobright.ai/jobs/info/aaa?utm_source=1103"
    assert rows[1]["posted"].date().isoformat() == "2026-10-14"
    assert skipped == 1


def test_jobright_date_across_new_year():
    jan1 = datetime(2027, 1, 1, 9, 0, tzinfo=timezone.utc)
    rows, _ = parse_jobright(JOBRIGHT.replace("Oct 15", "Dec 31"), jan1)
    assert rows[0]["posted"].date().isoformat() == "2026-12-31"


class Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def fetch(fmt, text, hours=30):
    entry = {"name": f"{fmt}-test", "format": fmt, "url": "https://raw.example/x"}
    return fetch_feed(entry, hours=hours, now=NOW, get=lambda url, **kw: Resp(text))


def test_fetch_feed_simplify_fields():
    jobs = fetch("simplify", SIMPLIFY)
    assert [j["company"] for j in jobs] == ["WPP", "Plus"]
    wpp, plus = jobs
    assert (wpp["ats"], wpp["detail_url"]) == (
        "greenhouse", "https://boards-api.greenhouse.io/v1/boards/wpp/jobs/8887554002")
    assert (wpp["feed"], wpp["feed_sponsorship"]) == ("simplify-test", "yes")
    assert (plus["feed_sponsorship"], plus["feed_sponsorship_raw"]) == (
        "no", "U.S. Citizenship is Required")
    assert wpp["source"] == "github/simplify-test"
    assert wpp["notes"] == "source: github/simplify-test"
    assert wpp["date_posted"] == "2026-10-15"


def test_fetch_feed_freshness_window():
    assert [j["company"] for j in fetch("speedyapply", SPEEDY)] == [
        "NVIDIA", "Acme", "Accenture Federal Services"]
    assert [j["role"] for j in fetch("jobright", JOBRIGHT)] == [
        "Software Engineer AV HIL (Early Career))", "Software Engineer, Platform"]


def test_fetch_feed_jobright_links_resolve_to_their_page():
    [gm, _] = fetch("jobright", JOBRIGHT)
    assert (gm["ats"], gm["detail_url"], gm["feed_sponsorship"]) == (
        "jobright", "https://jobright.ai/jobs/info/aaa", "")


def test_fetch_feed_unknown_link_has_no_detail_url():
    text = JOBRIGHT.replace("https://jobright.ai/jobs/info/aaa?utm_source=1103",
                            "https://apply.workable.com/acme/j/5A9494E612/")
    [gm, _] = fetch("jobright", text)
    assert (gm["ats"], gm["detail_url"]) == ("", "")


def test_fetch_feed_unknown_format_raises():
    with pytest.raises(KeyError):
        fetch_feed({"name": "x", "format": "rss", "url": "u"}, get=lambda *a, **k: Resp(""))
