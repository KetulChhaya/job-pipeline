# GitHub Feeds + Terminal Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three GitHub job lists (SimplifyJobs, speedyapply, jobright) become a no-LLM source lane, and one `jobhunt` command runs every lane with live progress, an end-of-lane summary, the log path and a macOS notification.

**Architecture:** `feeds.py` parses each list into the usual job dicts. `atslinks.py` maps an apply link to a description endpoint, and `enrich.fetch_detail` learns Greenhouse, Lever, Ashby and jobright. `progress.py` gives pipeline code a one-line progress channel (`logs/progress-<agent>.log`). `runner.py` wraps an agent CLI, or runs discover + add_job directly, shows progress and narration live, then summarizes from the `seen` registry. Shell scripts become thin wrappers; `run_all.sh` chains the lanes.

**Tech Stack:** Python 3.14 (`.venv`), `requests`, SQLite, bash, `osascript`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-15-feeds-and-runner-design.md` (read its "Revisions after the live prototype run" section too).

**Prototype note:** every code block was run in an isolated copy of this branch on 2026-10-15. Each task was replayed RED → GREEN in order (292 tests at the end), and the feeds lane was run end-to-end against the live feeds (287 fresh → 84 kept, 116 descriptions fetched, 33 sponsorship drops). Copy blocks exactly.

## Global Constraints

- Work on branch `feat/feeds-runner` (already holds the spec commits). Run tests with `.venv/bin/python -m pytest -q`.
- `discover.py` / `add_job.py` stdout JSON must stay unchanged: the agents parse it. Progress goes to the progress file only.
- The sheet stays append-only; `add_job.py` stays the only writer.
- Feed order in `config/boards.json` is simplify → speedyapply → jobright.
- The alias is `jobhunt`, not `jobs` (a zsh builtin).
- Never run `run_all.sh`, `run_claude.sh`, `run_gemini.sh` or `run_feeds.sh` without `--dry-run` before Task 9: they write to the sheet.
- Never stage `service-account.json`.

## Deliberate refinements of the spec

1. Progress lines always go to `logs/progress-<agent>.log` (or `$JOB_PROGRESS_FILE`). A probe showed the Claude CLI's Bash tool can't be relied on to pass custom env vars, so a fixed per-agent path is used.
2. The summary table shows the top 25 rows by score, then `… N more (all in the sheet, best first)`. The live dry run had 84 rows.
3. `--dry-run` summaries list the candidates that would be appended, since a dry run writes no registry rows.
4. jobright's `isH1bSponsor: false` is ignored (stays `sponsor ?`). Only `true` is used, as a fallback `sponsor ✓`.
5. The Gemini retry wait is a fixed 120s (the old shell loop waited 120s, then 240s).

## Review Focus

1. A feed changes its table shape (an added column, a new table). Expected: those rows are skipped and counted in a progress line; other feeds and lanes continue. Task 3, `test_parse_speedyapply_rows_and_ages` (5- and 6-cell tables) and `test_parse_jobright_continuation_rows_and_dates` (bad row counted).
2. An agent prints nothing for minutes, as agy does while it waits on background tasks. Expected: pipeline progress lines still appear live. Task 7, `test_lane_shows_progress_narration_and_summary` (the progress line is written before a sleep and checked in the output).
3. A jobright page without JobPosting data (blocked or redesigned). Expected: `desc ?`, no crash. Task 4, `test_jobright_page_without_job_posting`.
4. `run_claude.sh` run on its own, outside `run_all.sh`. Expected: its own summary and a notification. Task 7, `test_notify_respects_job_notify`.
5. Test runs must not write into the real `logs/` folder. Task 1, the `conftest.py` autouse fixture.

## File map

| File | Status | Responsibility |
|---|---|---|
| `pipeline/progress.py` | new | `init(agent)`, `progress(msg)`, `default_path(agent)` |
| `pipeline/atslinks.py` | new | `resolve(url) -> (ats, detail_url)` |
| `pipeline/feeds.py` | new | `parse_simplify`, `parse_speedyapply`, `parse_jobright`, `fetch_feed` |
| `pipeline/enrich.py` | modified | detail parsers for greenhouse / lever / ashby / jobright, feed sponsorship fallback |
| `config/profile.json` | modified | `level.feed_allow`; more citizenship / clearance wording |
| `pipeline/filters.py` | modified | `level_drop_feed` for feed jobs |
| `pipeline/discover.py` | modified | `github` source, progress lines, 3-day same company+title skip |
| `pipeline/add_job.py` | modified | one progress line |
| `config/boards.json` | modified | `github` feeds |
| `pipeline/runner.py` | new | lane runner, summary, notification, total |
| `run_claude.sh`, `run_gemini.sh` | rewritten | thin runner wrappers |
| `run_feeds.sh`, `run_all.sh` | new | feeds lane; everything + total |
| `README.md`, `~/.zshrc` | modified | usage; `jobhunt` alias |

---

### Task 1: Progress channel

**Files:**
- Create: `pipeline/progress.py`
- Modify: `tests/conftest.py` (full replacement below; adds an autouse fixture)
- Test: `tests/test_progress.py`

**Interfaces:**
- Produces: `progress.init(agent: str)`, `progress.progress(msg: str)` (never raises), `progress.default_path(agent) -> str` (`<repo>/logs/progress-<agent>.log`). `$JOB_PROGRESS_FILE` overrides the path.

- [ ] **Step 1: Write the failing test and the conftest fixture**

`tests/conftest.py`:

```python
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from filters import load_profile  # noqa: E402


@pytest.fixture(scope="session")
def prof():
    return load_profile(os.path.join(ROOT, "config", "profile.json"))


@pytest.fixture(autouse=True)
def _progress_to_tmp(tmp_path, monkeypatch):
    """Keep progress lines (and subprocesses' ones) out of the real logs/ folder."""
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(tmp_path / "progress.log"))
```

`tests/test_progress.py`:

```python
import progress


def test_lines_go_to_the_env_file(tmp_path, monkeypatch):
    path = tmp_path / "p.log"
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(path))
    progress.init("claude")
    progress.progress("fetching 3 boards")
    progress.progress("kept 1\n")
    assert path.read_text() == "fetching 3 boards\nkept 1\n"


def test_default_path_and_unwritable_file(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_PROGRESS_FILE")
    assert progress.default_path("gemini").endswith("logs/progress-gemini.log")
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(tmp_path / "missing-dir" / "x" / "p.log"))
    (tmp_path / "missing-dir").write_text("a file, so the folder can't be created")
    progress.init("claude")
    progress.progress("lost, and that's fine")   # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q`
Expected: `1 error` (collection: `No module named 'progress'`).

- [ ] **Step 3: Create `pipeline/progress.py`**

```python
"""One-line progress messages for the terminal runner (pipeline/runner.py).

discover.py and add_job.py call init(agent) once, then progress("...").
Lines go to $JOB_PROGRESS_FILE, else logs/progress-<agent>.log, which the
runner shows live. Writing never raises: a lost line must not break a run.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_path = None


def default_path(agent):
    return os.path.join(ROOT, "logs", f"progress-{agent}.log")


def init(agent):
    global _path
    _path = os.environ.get("JOB_PROGRESS_FILE") or default_path(agent)


def progress(msg):
    if not _path:
        return
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a") as f:
            f.write(msg.rstrip("\n") + "\n")
    except OSError:
        pass
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `240 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/progress.py tests/conftest.py tests/test_progress.py
git commit -m "feat(progress): one-line progress channel for the terminal runner"
```

---

### Task 2: ATS link resolver

**Files:**
- Create: `pipeline/atslinks.py`
- Test: `tests/test_atslinks.py`

**Interfaces:**
- Produces: `resolve(url) -> (ats, detail_url)`, where ats is one of `greenhouse`, `lever`, `ashby`, `workday`, `smartrecruiters`, `jobright`, or `(None, "")`. An Ashby detail_url is `<board list url>#<job id>`.

- [ ] **Step 1: Write the failing test**

`tests/test_atslinks.py`:

```python
import pytest

from atslinks import resolve


@pytest.mark.parametrize("url,expected", [
    ("https://job-boards.greenhouse.io/wpp/jobs/8887554002",
     ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/wpp/jobs/8887554002")),
    ("https://boards.greenhouse.io/robinhood/jobs/8126312?t=gh_src=&gh_jid=8126312",
     ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/robinhood/jobs/8126312")),
    ("https://boards.greenhouse.io/embed/job_app?for=stripe&token=7012345",
     ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/7012345")),
    ("https://jobs.lever.co/plus-2/49900d1b-1074-4372-82b0-a5f33c038a8c/apply",
     ("lever", "https://api.lever.co/v0/postings/plus-2/49900d1b-1074-4372-82b0-a5f33c038a8c")),
    ("https://jobs.ashbyhq.com/check-technologies/1647b178-a07b-4e62-b2e1-122e6fdbd134/application",
     ("ashby", "https://api.ashbyhq.com/posting-api/job-board/check-technologies"
               "#1647b178-a07b-4e62-b2e1-122e6fdbd134")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite/job/US-CA-Santa-Clara/"
     "SoC-Software-Engineer---New-College-Grad-2027_JR2026744",
     ("workday", "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/nvidiaexternalcareersite/"
                 "job/US-CA-Santa-Clara/SoC-Software-Engineer---New-College-Grad-2027_JR2026744")),
    ("https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/California---San-Francisco/"
     "Software-Engineer_JR123/apply?source=x",
     ("workday", "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/"
                 "job/California---San-Francisco/Software-Engineer_JR123")),
    ("https://jobs.smartrecruiters.com/Eurofins/744000155524286",
     ("smartrecruiters", "https://api.smartrecruiters.com/v1/companies/Eurofins/postings/744000155524286")),
    ("https://jobright.ai/jobs/info/6ab425877bd081371331602a?utm_source=1103",
     ("jobright", "https://jobright.ai/jobs/info/6ab425877bd081371331602a")),
    ("https://apply.workable.com/oxford-quantum-circuits-8/j/5A9494E612/apply", (None, "")),
    ("https://stripe.com/jobs/search?gh_jid=123", (None, "")),
    ("", (None, "")),
])
def test_resolve(url, expected):
    assert resolve(url) == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q`
Expected: `1 error` (`No module named 'atslinks'`).

- [ ] **Step 3: Create `pipeline/atslinks.py`**

```python
"""Map a job's apply link to the ATS endpoint that returns its description.

resolve(url) -> (ats, detail_url), or (None, "") for unknown hosts.
Ashby has no single-posting endpoint: its detail_url is the board list
plus "#<job id>", and enrich.fetch_detail picks the job out of the list.
jobright.ai pages are HTML; enrich.fetch_detail reads their JSON-LD.
"""
import re

_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_app\?for=([\w-]+)&token=(\d+)", re.I),
     "https://boards-api.greenhouse.io/v1/boards/{0}/jobs/{1}"),
    ("greenhouse", re.compile(r"(?:job-boards|boards)\.(?:eu\.)?greenhouse\.io/([\w-]+)/jobs/(\d+)", re.I),
     "https://boards-api.greenhouse.io/v1/boards/{0}/jobs/{1}"),
    ("lever", re.compile(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f]{8}-[0-9a-f-]{27})", re.I),
     "https://api.lever.co/v0/postings/{0}/{1}"),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f]{8}-[0-9a-f-]{27})", re.I),
     "https://api.ashbyhq.com/posting-api/job-board/{0}#{1}"),
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?"
                           r"([\w-]+)/(job/[^?#]+?)(?:/apply\b[^?#]*)?(?:[?#].*)?$"),
     "https://{0}.{1}.myworkdayjobs.com/wday/cxs/{0}/{2}/{3}"),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)/(\d+)", re.I),
     "https://api.smartrecruiters.com/v1/companies/{0}/postings/{1}"),
    # not an ATS, but the page embeds the posting as schema.org JobPosting JSON-LD
    ("jobright", re.compile(r"jobright\.ai/jobs/info/([0-9a-f]+)", re.I),
     "https://jobright.ai/jobs/info/{0}"),
]


def resolve(url):
    for ats, rx, template in _PATTERNS:
        m = rx.search(url or "")
        if m:
            return ats, template.format(*m.groups())
    return None, ""
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `252 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/atslinks.py tests/test_atslinks.py
git commit -m "feat(atslinks): map apply links to description endpoints"
```

---

### Task 3: Feed parsers

**Files:**
- Create: `pipeline/feeds.py`
- Test: `tests/test_feeds.py`

**Interfaces:**
- Consumes: `atslinks.resolve`, `fetchers._norm` / `UA` / `TIMEOUT`, `progress.progress`.
- Produces: `parse_simplify(text, now)`, `parse_speedyapply(text, now)`, `parse_jobright(text, now)` → `(rows, skipped)`, where each row is `{company, role, location, link, posted (aware datetime), sponsorship}`. `fetch_feed(entry, hours=30, now=None, get=None) -> list[job]`: the usual job dict plus `feed`, `feed_sponsorship` (`yes`/`no`/`""`) and `feed_sponsorship_raw`. `entry = {"name", "format", "url"}`; an unknown format raises `KeyError`.

- [ ] **Step 1: Write the failing test**

`tests/test_feeds.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q`
Expected: `1 error` (`No module named 'feeds'`).

- [ ] **Step 3: Create `pipeline/feeds.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `261 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/feeds.py tests/test_feeds.py
git commit -m "feat(feeds): SimplifyJobs, speedyapply and jobright list parsers"
```

---

### Task 4: Descriptions for feed links + sponsorship fallback

**Files:**
- Modify: `pipeline/enrich.py` (full replacement below)
- Modify: `config/profile.json` (full replacement below: `level.feed_allow`, more citizenship/clearance patterns)
- Test: `tests/test_enrich.py` (full replacement below; adds 10 tests)

**Interfaces:**
- Consumes: `fetchers._lever_text`, `fetchers.html_to_text`.
- Produces: `fetch_detail(job)` handles `ats` ∈ {smartrecruiters, workday, greenhouse, lever, ashby, jobright}. `_jobright_detail(page_html) -> (text, country)`; the text may end with a `[jobright] h1b_sponsor=yes|no` marker line. `enrich_job` falls back to `job["feed_sponsorship"]` (phrase `<feed>: <raw>`) when the description is silent, and turns an `h1b_sponsor=yes` marker into that fallback.

- [ ] **Step 1: Replace `tests/test_enrich.py`**

```python
import sqlite3

import pytest

from enrich import enrich_job, extract_sponsorship, extract_stack, init_cache, prune_cache


@pytest.mark.parametrize("text,status", [
    ("We are unable to sponsor visas for this role.", "no"),
    ("We are not able to sponsor employment visas.", "no"),
    ("We can’t sponsor visas at this time.", "no"),
    ("Company will not sponsor applicants for work visas.", "no"),
    ("Must be authorized to work in the US without the need for current or future visa sponsorship.", "no"),
    ("Candidates must be able to work without sponsorship now or in the future.", "no"),
    ("This position is not eligible for visa sponsorship.", "no"),
    ("Visa sponsorship is not available for this position.", "no"),
    ("No sponsorship is offered.", "no"),
    ("Must be a U.S. citizen.", "no"),
    ("U.S. citizenship is required.", "no"),
    ("Open to US citizens and green card holders.", "no"),
    ("Must have an active TS/SCI clearance.", "clearance"),
    ("Ability to obtain a security clearance is required.", "clearance"),
    ("This role requires access to ITAR-controlled data.", "clearance"),
    ("Applicants must be U.S. persons as defined by export regulations.", "clearance"),
    ("We are able to sponsor visas for this role.", "yes"),
    ("Visa sponsorship is available.", "yes"),
    ("H-1B transfers are supported.", "yes"),
    ("Candidates must be legally authorized to work in the United States.", "unknown"),
    ("We offer great benefits and remote work.", "unknown"),
    # C2: negation separated from "sponsor" by adverbs / "be able to"
    ("Unfortunately, we will not be able to sponsor visas for this role.", "no"),
    ("We won't be able to sponsor employment visas.", "no"),
    ("We aren't able to sponsor visas at this time.", "no"),
    ("We are not currently able to sponsor visas.", "no"),
    ("This team isn't able to sponsor H-1B visas.", "no"),
    ("Visa Sponsorship Available: No", "no"),
    ("Is visa sponsorship available for this role? No.", "no"),
    ("We do not currently sponsor visas.", "no"),
    ("We cannot currently sponsor.", "no"),
    ("We do not offer H-1B sponsorship.", "no"),
    ("We are not sponsoring visas.", "no"),
    ("Sponsorship will not be provided.", "no"),
    ("Must be authorized to work without employer sponsorship.", "no"),
    ("Must be able to work without needing sponsorship.", "no"),
    ("This role is not eligible for employer sponsorship.", "no"),
    ("Visa Sponsorship Available: Yes", "yes"),
    ("Whether or not you need sponsorship, we welcome all applicants.", "unknown"),
    # live jobright / Lockheed Martin wording
    ("U.S. citizenship is a requirement for consideration.", "no"),
    ("This position requires U.S. citizenship.", "no"),
    ("Selected candidates will be required to obtain a government security clearance before starting.",
     "clearance"),
    ("We hire without regard to race, religion, or citizenship status.", "unknown"),
    # C1: export-licence boilerplate is not a work-visa statement
    ("This position may require access to information protected under U.S. export control laws. "
     "Please note that any offer of employment may be conditioned on your authorization to receive "
     "software or technology controlled under these U.S. export laws without sponsorship for an "
     "export license.", "unknown"),
    ("", "unknown"),
])
def test_extract_sponsorship(prof, text, status):
    assert extract_sponsorship(text, prof)[0] == status


def test_sponsorship_phrase_is_returned(prof):
    assert extract_sponsorship("We will not sponsor visas.", prof) == ("no", "will not sponsor")


def test_extract_stack_puts_user_stack_first(prof):
    text = ("You will work with AWS, Kubernetes and Java services, plus React and "
            "TypeScript frontends and Python data jobs. Bonus: LLM and RAG experience.")
    assert extract_stack(text, prof) == ["Python", "TypeScript", "React", "Java",
                                         "AWS", "Kubernetes"]


def test_extract_stack_ignores_lowercase_common_words(prof):
    assert extract_stack("we react to change, go fast, and start in spring", prof) == []


def _job(**kw):
    base = {"role": "Software Engineer", "job_link": "https://example.com/jobs/1",
            "ats": "workday", "detail_url": "https://example.com/detail/1",
            "description": "", "country": "", "tags": []}
    base.update(kw)
    return base


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    init_cache(c)
    return c


def test_enrich_uses_inline_description_without_fetching(prof, con):
    def boom(job):
        raise AssertionError("should not fetch")
    j = enrich_job(_job(detail_url="", description="3+ years of experience with Python and LLMs."),
                   prof, con=con, fetch=boom)
    assert (j["yoe_min"], j["yoe_max"]) == (3, None)
    assert j["user_groups"] == ["python"]
    assert j["ai_groups"] == ["llm"]
    assert j["applied_ai_hit"] is True
    assert "desc ?" not in j["tags"]


def test_enrich_fetches_once_then_uses_cache(prof, con):
    calls = []

    def fake(job):
        calls.append(job["detail_url"])
        return "4+ years of experience with TypeScript.", "United States of America"
    a = enrich_job(_job(), prof, con=con, fetch=fake)
    b = enrich_job(_job(), prof, con=con, fetch=fake)
    assert len(calls) == 1
    assert a["yoe_min"] == b["yoe_min"] == 4
    assert b["country"] == "United States of America"


def test_enrich_fetch_failure_keeps_job_tagged(prof, con):
    def fail(job):
        raise ConnectionError("404")
    j = enrich_job(_job(), prof, con=con, fetch=fail)
    assert j["description"] == ""
    assert j["tags"] == ["desc ?"]
    assert j["yoe_min"] is None and j["sponsorship"] == "unknown"
    assert con.execute("SELECT COUNT(*) FROM desc_cache").fetchone()[0] == 0


def test_enrich_title_counts_toward_stack(prof, con):
    j = enrich_job(_job(role="Sr Software Engineer (Python/Typescript)", detail_url="",
                        description="Build APIs."), prof, con=con)
    assert set(j["user_groups"]) == {"python", "typescript"}


def test_cache_write_does_not_hold_the_db_lock(prof, tmp_path):
    db = str(tmp_path / "seen.db")
    a = sqlite3.connect(db)
    init_cache(a)
    a.commit()
    other = sqlite3.connect(db, timeout=0.2)
    other.execute("CREATE TABLE t(x)")
    other.commit()

    def fetch(job):
        other.execute("INSERT INTO t VALUES (1)")  # a second writer mid-run (add_job)
        other.commit()
        return "3+ years of experience.", ""
    enrich_job(_job(job_link="https://e/1"), prof, con=a, fetch=fetch)
    enrich_job(_job(job_link="https://e/2"), prof, con=a, fetch=fetch)
    assert other.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_prune_cache_commits(tmp_path):
    c = sqlite3.connect(str(tmp_path / "seen.db"))
    init_cache(c)
    c.commit()
    prune_cache(c)
    assert not c.in_transaction


def test_prune_cache_drops_old_rows(con):
    con.execute("INSERT INTO desc_cache VALUES ('old','x','','2026-01-01T00:00:00')")
    con.execute("INSERT INTO desc_cache VALUES ('new','x','','2999-01-01T00:00:00')")
    prune_cache(con, days=3)
    assert [r[0] for r in con.execute("SELECT url_key FROM desc_cache")] == ["new"]


def test_feed_sponsorship_used_when_text_is_silent(prof):
    j = enrich_job(_job(detail_url="", description="3+ years of experience.",
                        feed="simplify-newgrad", feed_sponsorship="no",
                        feed_sponsorship_raw="U.S. Citizenship is Required"), prof)
    assert (j["sponsorship"], j["sponsorship_phrase"]) == (
        "no", "simplify-newgrad: U.S. Citizenship is Required")


def test_description_beats_feed_sponsorship(prof):
    j = enrich_job(_job(detail_url="", description="Visa sponsorship is available.",
                        feed="simplify-newgrad", feed_sponsorship="no"), prof)
    assert j["sponsorship"] == "yes"


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.fixture
def fake_http(monkeypatch):
    import enrich
    calls = []

    def get(url, **kw):
        calls.append(url)
        return FakeResp(PAYLOADS[url.split("?")[0]])
    monkeypatch.setattr(enrich.requests, "get", get)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    monkeypatch.setattr(enrich, "_ASHBY_BOARDS", {})
    return calls


PAYLOADS = {
    "https://boards-api.greenhouse.io/v1/boards/wpp/jobs/1":
        {"content": "&lt;p&gt;4+ years of experience with Python&lt;/p&gt;"},
    "https://api.lever.co/v0/postings/acme/u1":
        {"descriptionPlain": "About.", "country": "US",
         "lists": [{"text": "Requirements", "content": "<li>3+ years of experience</li>"}]},
    "https://api.ashbyhq.com/posting-api/job-board/check":
        {"jobs": [{"id": "a1", "descriptionPlain": "2+ years of experience with TypeScript.",
                   "address": {"postalAddress": {"addressCountry": "United States"}}},
                  {"id": "a2", "descriptionPlain": "6+ years of experience."}]},
}


def test_fetch_detail_greenhouse_and_lever(fake_http):
    from enrich import fetch_detail
    assert fetch_detail({"ats": "greenhouse", "detail_url": "https://boards-api.greenhouse.io/v1/boards/wpp/jobs/1"}) == (
        "4+ years of experience with Python", "")
    text, country = fetch_detail({"ats": "lever", "detail_url": "https://api.lever.co/v0/postings/acme/u1"})
    assert country == "US" and "3+ years of experience" in text


def test_fetch_detail_ashby_fetches_board_once(fake_http):
    from enrich import fetch_detail
    base = "https://api.ashbyhq.com/posting-api/job-board/check"
    assert fetch_detail({"ats": "ashby", "detail_url": base + "#a1"}) == (
        "2+ years of experience with TypeScript.", "United States")
    assert fetch_detail({"ats": "ashby", "detail_url": base + "#a2"})[0] == "6+ years of experience."
    assert fake_http == [base]
    with pytest.raises(LookupError):
        fetch_detail({"ats": "ashby", "detail_url": base + "#missing"})


JOBRIGHT_PAGE = """<html><script type="application/ld+json">{"@context": "https://schema.org/",
 "@type": "JobPosting", "title": "Software Engineer I",
 "description": "&lt;p&gt;1+ years of experience with Java.&lt;/p&gt;",
 "jobLocation": {"address": {"addressLocality": "San Francisco", "addressCountry": "US"}}}</script>
<script id="__NEXT_DATA__">{"props": {"isH1bSponsor":true, "h1bAnnualJobCount": []}}</script></html>"""


def test_jobright_page_description_country_and_h1b(prof, monkeypatch):
    import enrich

    class Page:
        text = JOBRIGHT_PAGE

        def raise_for_status(self):
            pass
    monkeypatch.setattr(enrich.requests, "get", lambda url, **kw: Page())
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    text, country = enrich.fetch_detail({"ats": "jobright", "detail_url": "https://jobright.ai/jobs/info/x"})
    assert country == "US"
    assert text.splitlines() == ["1+ years of experience with Java.", "[jobright] h1b_sponsor=yes"]
    j = enrich_job(_job(ats="jobright", detail_url="https://jobright.ai/jobs/info/x",
                        feed="jobright-swe-newgrad"), prof, fetch=lambda job: (text, country))
    assert (j["yoe_min"], j["country"]) == (1, "US")
    assert (j["sponsorship"], j["sponsorship_phrase"]) == (
        "yes", "jobright-swe-newgrad: H-1B sponsor history")


def test_jobright_page_without_job_posting(monkeypatch):
    from enrich import _jobright_detail
    assert _jobright_detail("<html>no data</html>") == ("", "")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q`
Expected: `6 failed, 263 passed, 2 errors`. The failures are the new citizenship/clearance strings, the feed fallback, the Greenhouse/Lever and jobright details; the errors are the Ashby fixture's missing `_ASHBY_BOARDS` and the `_jobright_detail` import.

- [ ] **Step 3: Replace `pipeline/enrich.py`**

```python
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
```

- [ ] **Step 4: Replace `config/profile.json`**

```json
{
 "_comment": "Candidate filter profile. Agents must NOT edit this file. Spec: docs/superpowers/specs/2026-10-15-job-filters-design.md. Patterns are case-insensitive regex fragments matched on whitespace-collapsed text with letter/digit boundaries; wrap a part in (?-i:...) to make it case-sensitive.",
 "max_yoe": 5,
 "senior_unknown_yoe": "keep",
 "per_company_per_day": 3,
 "needs_sponsorship": true,
 "location": {
  "ambiguous_remote": "keep",
  "us_cities": [
   "san francisco", "sf", "bay area", "seattle", "new york city", "nyc", "brooklyn",
   "boston", "austin", "los angeles", "chicago", "denver", "boulder", "atlanta",
   "sunnyvale", "mountain view", "palo alto", "menlo park", "san jose", "santa clara",
   "cupertino", "san mateo", "redwood city", "pleasanton", "irvine", "san diego",
   "redmond", "bellevue", "kirkland", "pittsburgh", "philadelphia", "miami", "dallas",
   "houston", "portland", "salt lake city", "raleigh", "nashville", "phoenix",
   "minneapolis", "detroit", "columbus", "charlotte", "st\\.? louis", "kansas city",
   "jersey city", "hoboken"
  ],
  "non_us_strong": [
   "canada", "toronto", "montreal", "ottawa", "waterloo", "calgary",
   "ontario", "quebec", "british columbia", "brazil", "brasil", "s[ãa]o paulo", "mexico",
   "india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "gurgaon",
   "gurugram", "noida", "mumbai", "united kingdom", "uk", "england", "scotland",
   "ireland", "germany", "berlin", "munich", "france", "spain", "madrid", "barcelona",
   "netherlands", "amsterdam", "poland", "warsaw", "krakow", "singapore", "japan",
   "tokyo", "australia", "sydney", "melbourne", "israel", "tel aviv", "portugal",
   "lisbon", "romania", "bucharest", "serbia", "belgrade", "argentina", "buenos aires",
   "colombia", "bogota", "chile", "philippines", "manila", "vietnam", "china", "beijing",
   "shanghai", "shenzhen", "hong kong", "korea", "seoul", "taiwan", "taipei", "czech",
   "prague", "sweden", "stockholm", "switzerland", "zurich", "italy", "milan", "belgium",
   "brussels", "denmark", "copenhagen", "finland", "helsinki", "norway", "oslo",
   "austria", "vienna", "hungary", "budapest", "ukraine", "kyiv", "turkey", "istanbul",
   "egypt", "cairo", "nigeria", "lagos", "kenya", "nairobi", "south africa", "uae",
   "dubai", "abu dhabi", "saudi", "riyadh", "pakistan", "new zealand", "greece",
   "athens", "estonia", "lithuania", "latvia", "bulgaria", "croatia", "costa rica",
   "uruguay", "peru", "latam", "emea", "apac", "europe", "asia"
  ],
  "non_us_weak": ["london", "dublin", "paris", "cambridge", "vancouver"]
 },
 "family": {
  "keep": [
   "software engineer(?:ing)?", "software developer", "software development engineer",
   "sde", "mts", "member of (?:the )?technical staff", "back-?end", "back end",
   "full-?stack", "full stack", "ai engineer", "applied ai", "llm engineer",
   "gen ?ai engineer", "generative ai engineer", "agents? engineer", "product engineer",
   "api engineer", "software platform engineer", "machine learning engineer", "ml engineer"
  ],
  "drop": [
   "system software", "systems software", "soc", "firmware", "embedded", "kernel",
   "drivers?", "cuda", "asic", "fpga", "silicon", "eda", "validation engineer",
   "verification engineer", "design verification", "system validation", "hardware",
   "mfg", "manufacturing", "security engineer", "cybersecurity", "penetration", "pen ?test(?:ing|er)?", "sales engineer",
   "solutions? engineer", "support engineer", "solutions architect", "devops", "sre",
   "site reliability", "data engineer", "mobile", "ios", "android", "client platform",
   "network engineer", "(?<!software )platform engineer", "sdet", "qa engineer",
   "test engineer", "software engineer in test", "quality engineer"
  ],
  "frontend": ["front-?end", "front end"],
  "ml_titles": ["machine learning engineer", "ml engineer"],
  "ml_requires_any": [
   "llms?", "large language models?", "rag", "retrieval[- ]augmented", "agents?",
   "agentic", "gen ?ai", "generative ai", "bedrock", "langchain", "prompt(?:s|ing)?"
  ]
 },
 "level": {
  "drop": [
   "staff", "principal", "lead", "architect", "distinguished", "fellow", "manager",
   "director", "head of", "vp", "vice president", "intern", "internship",
   "new grad(?:uate)?", "new college grad(?:uate)?", "college grad(?:uate)?",
   "graduate", "university", "amts", "lmts", "pmts"
  ],
  "senior": ["senior", "sr\\.?", "snr", "smts"],
  "feed_allow": ["new grad(?:uate)?", "new college grad(?:uate)?", "college grad(?:uate)?",
                 "graduate", "university", "amts"]
 },
 "citizenship_title": ["govcloud", "federal", "cleared", "clearance", "ts/sci", "top secret", "secret"],
 "sponsorship": {
  "negative": [
   "(?:(?:will|would|do|does|did|are|is|can|could) )?(?:unable|cannot|can ?not|[a-z]+n't|not|never|no longer)(?: (?:currently|presently|yet|be|able|to|in a position to|going to|at this time))* (?:to )?(?:provide |offer |support |grant )?(?:visa |immigration |employment |work |h-?1b )*sponsor(?:ship|ing)?",
   "without (?:the need for |needing |requiring )?(?:current or future |now or in the future |future )?(?:visa |employment |employer |immigration |work )?sponsorship(?! for an? export)",
   "no (?:visa |immigration )?sponsorship",
   "sponsorship (?:is |will )?not (?:be )?(?:available|offered|provided|supported)",
   "not (?:eligible|available) for (?:visa |immigration |employer |employment )?sponsorship",
   "sponsorship available(?: for this (?:role|position|job))?(?: ?[?:-])? no",
   "must be a u\\.?s\\.? citizen",
   "u\\.?s\\.? citizen(?:ship)? (?:is )?(?:required|only)",
   "u\\.?s\\.? citizens only",
   "(?:u\\.?s\\.? )?citizenship is (?:a )?requirement",
   "requires? (?:u\\.?s\\.? )?citizenship",
   "green card holders?"
  ],
  "clearance": [
   "(?:active|current|ability to obtain|able to obtain|eligib(?:le|ility) (?:for|to obtain)|must (?:obtain|hold|possess|have)) (?:an? |and maintain (?:an? )?)?(?:[a-z/]+ ){0,3}?clearance",
   "obtain (?:and maintain )?(?:an? )?(?:[a-z/]+ ){0,3}?clearance",
   "ts/sci", "top secret", "secret clearance", "itar", "u\\.?s\\.? persons?"
  ],
  "positive": [
   "(?:will|can|able to|happy to|glad to) sponsor",
   "(?:visa )?sponsorship (?:is )?available",
   "h-?1b (?:visa )?(?:sponsorship|transfers?) (?:is |are )?(?:available|supported|welcome)",
   "we sponsor (?:visas|h-?1b)"
  ]
 },
 "stack": {
  "user_groups": {
   "typescript": "typescript|javascript",
   "node": "node\\.?js|(?-i:Node)|nest\\.?js|express\\.?js",
   "react": "react\\.?js|(?-i:React)|next\\.?js",
   "python": "python",
   "python_web": "fastapi|django|(?-i:Flask)"
  },
  "ai_groups": {
   "llm": "llms?|large language models?|gen ?ai|generative ai",
   "rag": "rag|retrieval[- ]augmented|embeddings?|vector (?:db|database|store|search)|pgvector|pinecone|weaviate",
   "agents": "ai agents?|agentic|multi-agent|llm agents?|agent (?:framework|development|platform|orchestration)",
   "frameworks": "(?-i:Bedrock)|langchain|llamaindex|openai|anthropic|(?-i:Claude)",
   "prompt": "prompt engineering|prompt design|prompting|system prompts?"
  },
  "user_terms": ["Python", "TypeScript", "JavaScript", "Node.js", "React", "Next.js", "FastAPI", "Django", "Flask"],
  "dictionary": {
   "Python": "python", "TypeScript": "typescript", "JavaScript": "javascript",
   "Node.js": "node\\.?js|(?-i:Node)", "React": "react\\.?js|(?-i:React)",
   "Next.js": "next\\.?js", "FastAPI": "fastapi", "Django": "django", "Flask": "(?-i:Flask)",
   "Java": "(?-i:Java)", "Kotlin": "kotlin", "Go": "golang|(?-i:Go)", "Rust": "(?-i:Rust)",
   "C++": "c\\+\\+", "C#": "c#", "Ruby": "ruby", "Scala": "(?-i:Scala)",
   "GraphQL": "graphql", "Express": "express\\.?js", "NestJS": "nest\\.?js",
   "Spring": "spring boot|(?-i:Spring)", "Rails": "ruby on rails|(?-i:Rails)", ".NET": "\\.net",
   "AWS": "aws|amazon web services", "GCP": "gcp|google cloud", "Azure": "(?-i:Azure)",
   "Kubernetes": "kubernetes|k8s", "Docker": "docker", "Terraform": "terraform",
   "Lambda": "(?-i:Lambda)", "Postgres": "postgres(?:ql)?", "MySQL": "mysql",
   "MongoDB": "mongo(?:db)?", "Redis": "redis", "Kafka": "kafka", "Spark": "(?-i:Spark)",
   "Snowflake": "(?-i:Snowflake)", "DynamoDB": "dynamo(?:db)?",
   "LLM": "llms?|large language models?|gen ?ai|generative ai", "RAG": "rag|retrieval[- ]augmented",
   "Agents": "ai agents?|agentic|multi-agent|llm agents?", "Bedrock": "(?-i:Bedrock)",
   "LangChain": "langchain", "OpenAI": "openai", "Claude": "anthropic|(?-i:Claude)",
   "Embeddings": "embeddings?",
   "Vector DB": "vector (?:db|database|store|search)|pgvector|pinecone|weaviate|qdrant",
   "PyTorch": "pytorch"
  }
 },
 "weights": {
  "user_stack_per_group": 10,
  "ai_per_group": 5,
  "yoe_known": 15,
  "yoe_unknown": 8,
  "sponsor_yes": 10,
  "sponsor_unknown": 5
 }
}
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `271 passed`.

- [ ] **Step 6: Commit**

```bash
git add pipeline/enrich.py config/profile.json tests/test_enrich.py
git commit -m "feat(enrich): descriptions for greenhouse/lever/ashby/jobright links, feed sponsorship fallback"
```

---

### Task 5: New-grad titles allowed for feed jobs

**Files:**
- Modify: `pipeline/filters.py` (full replacement below)
- Test: `tests/test_title.py` (full replacement below; adds 7 tests)

**Interfaces:**
- Consumes: `profile.level.feed_allow` (Task 4).
- Produces: `prof["_re"]["level_drop_feed"]`. `quick_check` uses it when `job.get("feed")` is set.

- [ ] **Step 1: Replace `tests/test_title.py`**

```python
import pytest

from filters import is_senior, quick_check


def job(role, location="New York, NY", country=""):
    return {"role": role, "location": location, "country": country}


@pytest.mark.parametrize("role,reason", [
    ("Software Engineering Manager", "level"),
    ("Manager, Software Engineering (App Infrastructure)", "level"),
    ("Staff+ Software Engineer, Platform Acceleration", "level"),
    ("Principal Software Engineer I - Platform Billing Experience", "level"),
    ("Real-time Data Platform Software Engineer- Senior/Lead", "level"),
    ("Software Engineering AMTS (College Grad)", "level"),
    ("Software Engineer Intern", "level"),
    ("Senior System Software Engineer, ML and Vector Search", "family"),
    ("SoC Software Engineer - New College Grad 2027", "family"),
    ("Embedded Firmware and Software Engineer", "family"),
    ("Client Platform Engineer", "family"),
    ("Senior Compute Platform Engineer, LSF - EDA Infrastructure", "family"),
    ("DevOps Engineer", "family"),
    ("FinOps Data Engineer", "family"),
    ("Frontend Engineer", "family"),
    ("Senior Android Engineer", "family"),
    ("Software Engineer in Test", "family"),
    ("Sales Engineer", "family"),
    ("Product Manager", "family"),
    ("GovCloud Software Engineer", "sponsorship"),
    ("Software Development Engineer (US Federal)", "sponsorship"),
    ("Penetration Testing Engineer, MTS", "family"),
])
def test_quick_check_drops(prof, role, reason):
    c = quick_check(job(role), prof)
    assert (c.keep, c.reason) == (False, reason)


@pytest.mark.parametrize("role", [
    "Software Engineer",
    "Software Engineer II",
    "Senior Software Engineer",
    "Software Engineering MTS",
    "Senior Member of Technical Staff",
    "SDE II",
    "Software Development Engineer",
    "Backend Engineer",
    "Sr. Backend Engineer II, AutoScaling & Balancing, NG-SIEM (Hybrid)",
    "Full Stack / Frontend Engineer",
    "Senior Full-Stack Software Engineer, Storage Management Platform - DGX Cloud",
    "AI Engineer",
    "Engineer, Applied AI",
    "Software Engineer, Identity Verification",
    "Sr Software Engineer – API Platform, Product Engineering (Python/Typescript)",
    "Machine Learning Engineer",
    "Data Platform Software Engineer",
    "Software Platform Engineer",
])
def test_quick_check_keeps(prof, role):
    c = quick_check(job(role), prof)
    assert c.keep, c


def test_ambiguous_location_is_kept_and_tagged(prof):
    c = quick_check(job("Software Engineer", "Remote, Global"), prof)
    assert c.keep and c.tags == ("loc ?",)


def test_non_us_location_reason(prof):
    c = quick_check(job("Software Engineer", "Remote, Israel"), prof)
    assert (c.keep, c.reason) == (False, "location")


def test_govcloud_kept_when_sponsorship_not_needed(prof):
    relaxed = dict(prof, needs_sponsorship=False)
    assert quick_check(job("GovCloud Software Engineer"), relaxed).keep


@pytest.mark.parametrize("role,expected", [
    ("Senior Software Engineer", True),
    ("Sr. Backend Engineer", True),
    ("Software Engineering SMTS", True),
    ("Senior Member of Technical Staff", True),
    ("Software Engineer II", False),
    ("Member of Technical Staff", False),
])
def test_is_senior(prof, role, expected):
    assert is_senior(role, prof) is expected


@pytest.mark.parametrize("role", [
    "Software Engineer - New Grad 2026",
    "Software Engineer, New College Graduate",
    "Software Engineering AMTS (College Grad)",
    "University Graduate - Backend Engineer",
])
def test_feed_jobs_keep_new_grad_titles(prof, role):
    assert not quick_check(job(role), prof).keep          # board lanes: still dropped
    assert quick_check(dict(job(role), feed="simplify-newgrad"), prof).keep


@pytest.mark.parametrize("role", ["Software Engineer Intern", "Staff Software Engineer",
                                  "Software Engineering Manager, Platform"])
def test_feed_jobs_still_drop_other_levels(prof, role):
    assert quick_check(dict(job(role), feed="simplify-newgrad"), prof).reason == "level"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q`
Expected: `4 failed, 274 passed` (the four `test_feed_jobs_keep_new_grad_titles` cases).

- [ ] **Step 3: Replace `pipeline/filters.py`**

```python
"""Keep/drop decisions for normalized job dicts, driven by config/profile.json.

quick_check: title + location, before any description is fetched.
full_check:  description facts that enrich.enrich_job wrote into the job.
score / merge / cap: ranking and per-company limits for the survivors.

Every check returns Check(keep, reason, detail, tags). Nothing here does I/O
except load_profile.
"""
import json
import re
from collections import namedtuple

Check = namedtuple("Check", "keep reason detail tags")

REQUIRED_KEYS = ["max_yoe", "senior_unknown_yoe", "per_company_per_day",
                 "needs_sponsorship", "location", "family", "level",
                 "citizenship_title", "sponsorship", "stack", "weights"]

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
]
# Upper-case only, matched case-sensitively. IN is left out on purpose:
# "Bengaluru, IN" is India far more often than Indiana.
US_STATE_CODES = re.compile(
    r"(?<![A-Za-z0-9])(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IA|KS|KY|LA|ME|"
    r"MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|"
    r"UT|VT|VA|WA|WV|WI|WY|DC)(?![A-Za-z0-9])")
US_ALIASES = {"us", "usa", "u.s.", "u.s.a.", "united states",
              "united states of america"}
_US_WORDS = r"united states(?: of america)?|u\.s\.(?:a\.)?|usa|(?-i:US)"
_SEGMENT_SPLIT = re.compile(r"[;|\n/]| or ")
_MTS_LONG = re.compile(r"member of (?:the )?technical staff", re.I)


def _rx(fragments):
    """Case-insensitive alternation bounded by non-alphanumerics."""
    if isinstance(fragments, str):
        fragments = [fragments]
    if not fragments:
        return re.compile(r"(?!x)x")  # never matches
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(fragments) + r")(?![a-z0-9])",
                      re.I)


def _collapse(s):
    return " ".join((s or "").split())


def load_profile(path):
    """Load and compile config/profile.json. Raises ValueError on any problem."""
    try:
        with open(path) as f:
            prof = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read profile {path}: {e}") from e
    missing = [k for k in REQUIRED_KEYS if k not in prof]
    if missing:
        raise ValueError(f"profile {path} missing keys: {', '.join(missing)}")
    loc, fam, lvl = prof["location"], prof["family"], prof["level"]
    sp, st = prof["sponsorship"], prof["stack"]
    try:
        prof["_re"] = {
            "us_states": _rx(US_STATES),
            "us_aliases": _rx(_US_WORDS),
            "us_cities": _rx(loc["us_cities"]),
            "non_us_strong": _rx(loc["non_us_strong"]),
            "non_us_weak": _rx(loc["non_us_weak"]),
            "family_keep": _rx(fam["keep"]),
            "family_drop": _rx(fam["drop"]),
            "frontend": _rx(fam["frontend"]),
            "ml_titles": _rx(fam["ml_titles"]),
            "ml_requires_any": _rx(fam["ml_requires_any"]),
            "level_drop": _rx(lvl["drop"]),
            "level_drop_feed": _rx([f for f in lvl["drop"] if f not in lvl.get("feed_allow", [])]),
            "level_senior": _rx(lvl["senior"]),
            "citizenship_title": _rx(prof["citizenship_title"]),
            "sponsor_neg": _rx(sp["negative"]),
            "sponsor_pos": _rx(sp["positive"]),
            "clearance": _rx(sp["clearance"]),
            "user_groups": {k: _rx(v) for k, v in st["user_groups"].items()},
            "ai_groups": {k: _rx(v) for k, v in st["ai_groups"].items()},
            "dictionary": [(name, _rx(p)) for name, p in st["dictionary"].items()],
        }
    except KeyError as e:
        raise ValueError(f"profile {path} missing key: {e}") from e
    except re.error as e:
        raise ValueError(f"profile {path} has a bad pattern: {e}") from e
    return prof


# ------------------------------------------------------------------ location
def _segment_kind(seg, R):
    # Explicit US evidence (US / USA, a state name, a US city) wins: "Toronto
    # Canada, San Francisco, Remote in US" is open to US applicants. A foreign
    # place name only overrides a bare two-letter code ("Toronto, CA").
    if R["us_aliases"].search(seg) or R["us_states"].search(seg) or R["us_cities"].search(seg):
        return "us"
    if R["non_us_strong"].search(seg):
        return "non_us"
    if US_STATE_CODES.search(seg):
        return "us"
    if R["non_us_weak"].search(seg):
        return "non_us"
    return "unknown"


def _country_kind(country, prof):
    """Structured country -> 'us' | 'non_us'. Handles ISO codes ('us', 'in')
    and free text such as Ashby's 'US | EU'."""
    c = country.strip()
    if c.lower() in US_ALIASES:
        return "us"
    if len(c) == 2 and c.isalpha():
        return "non_us"
    kinds = [_segment_kind(s.strip(), prof["_re"]) for s in _SEGMENT_SPLIT.split(c) if s.strip()]
    return "us" if "us" in kinds else "non_us"


def classify_location(location, country, prof):
    """'us' | 'non_us' | 'ambiguous'. A structured country always wins."""
    if (country or "").strip():
        return _country_kind(country, prof)
    text = _collapse(location)
    if not text:
        return "ambiguous"
    kinds = [_segment_kind(s.strip(), prof["_re"])
             for s in _SEGMENT_SPLIT.split(text) if s.strip()]
    if "us" in kinds:
        return "us"
    if "non_us" in kinds:
        return "non_us"
    return "ambiguous"


# --------------------------------------------------------------------- title
def _level_title(title_l):
    return _MTS_LONG.sub("mts", title_l)


def is_senior(title, prof):
    return bool(prof["_re"]["level_senior"].search(_level_title(_collapse(title).lower())))


def _family_drop(title_l, R):
    """Matched drop term, 'not-swe', or None when the family is wanted."""
    m = R["family_drop"].search(title_l)
    if m:
        return m.group(0)
    m = R["frontend"].search(title_l)
    if m and "full" not in title_l:
        return m.group(0)
    if not R["family_keep"].search(title_l):
        return "not-swe"
    return None


def quick_check(job, prof):
    R = prof["_re"]
    tl = _collapse(job.get("role")).lower()
    tags = ()
    where = classify_location(job.get("location"), job.get("country"), prof)
    if where == "non_us":
        return Check(False, "location", job.get("country") or job.get("location") or "", ())
    if where == "ambiguous":
        if prof["location"]["ambiguous_remote"] == "drop":
            return Check(False, "location", "ambiguous", ())
        tags = ("loc ?",)
    fam = _family_drop(tl, R)
    if fam:
        return Check(False, "family", fam, ())
    level = R["level_drop_feed"] if job.get("feed") else R["level_drop"]
    m = level.search(_level_title(tl))
    if m:
        return Check(False, "level", m.group(0), ())
    if prof["needs_sponsorship"]:
        m = R["citizenship_title"].search(tl)
        if m:
            return Check(False, "sponsorship", m.group(0), ())
    return Check(True, None, None, tags)


# --------------------------------------------------------------- description
def full_check(job, prof):
    """Uses fields set by enrich.enrich_job: country, description, yoe_min,
    sponsorship, sponsorship_phrase, applied_ai_hit."""
    country = (job.get("country") or "").strip()
    if country and _country_kind(country, prof) != "us":
        return Check(False, "location", country, ())
    yoe = job.get("yoe_min")
    if yoe is not None and yoe > prof["max_yoe"]:
        return Check(False, "yoe", f"{yoe}+", ())
    if (yoe is None and prof["senior_unknown_yoe"] == "drop"
            and is_senior(job.get("role"), prof)):
        return Check(False, "yoe", "senior, yoe unknown", ())
    status = job.get("sponsorship", "unknown")
    if status == "clearance":
        return Check(False, "sponsorship", job.get("sponsorship_phrase", ""), ())
    if status == "no" and prof["needs_sponsorship"]:
        return Check(False, "sponsorship", job.get("sponsorship_phrase", ""), ())
    tl = _collapse(job.get("role")).lower()
    if (prof["_re"]["ml_titles"].search(tl) and job.get("description")
            and not job.get("applied_ai_hit")):
        return Check(False, "family", "ml-not-applied-ai", ())
    return Check(True, None, None, ())


# ------------------------------------------------------ score / merge / cap
def score(job, prof):
    w = prof["weights"]
    pts = len(job.get("user_groups", [])) * w["user_stack_per_group"]
    pts += len(job.get("ai_groups", [])) * w["ai_per_group"]
    pts += w["yoe_known"] if job.get("yoe_min") is not None else w["yoe_unknown"]
    pts += w["sponsor_yes"] if job.get("sponsorship") == "yes" else w["sponsor_unknown"]
    return min(100, pts)


def company_key(name):
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in ("inc", "llc", "ltd"))


def title_key(title):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split())


def merge(jobs):
    """Collapse same company + title. Returns (merged, folded) where folded is
    a list of (job, winner_link) for the rows that were absorbed."""
    groups, order = {}, []
    for j in jobs:
        k = (company_key(j.get("company")), title_key(j.get("role")))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(j)
    merged, folded = [], []
    for k in order:
        g = sorted(groups[k], key=lambda j: -j.get("score", 0))
        win = dict(g[0])
        if len(g) > 1:
            locs = []
            for j in g:
                loc = (j.get("location") or "").strip()
                if loc and loc not in locs:
                    locs.append(loc)
            if len(locs) > 2:
                win["location"] = f"{locs[0]}; {locs[1]} (+{len(locs) - 2} more)"
            elif locs:
                win["location"] = "; ".join(locs)
            folded += [(j, win["job_link"]) for j in g[1:]]
        merged.append(win)
    return merged, folded


def cap(jobs, prof, already_today):
    """Keep at most per_company_per_day per company, best score first.
    already_today: {company_key: rows appended today}. Returns (kept, capped)
    where capped is a list of (job, detail)."""
    limit = prof["per_company_per_day"]
    taken = dict(already_today)
    kept, capped = [], []
    for j in sorted(jobs, key=lambda j: -j.get("score", 0)):
        k = company_key(j.get("company"))
        n = taken.get(k, 0)
        if n >= limit:
            capped.append((j, f"{k}: {n} today"))
            continue
        taken[k] = n + 1
        kept.append(j)
    return kept, capped
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `278 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/filters.py tests/test_title.py
git commit -m "feat(filters): feed jobs keep new-grad titles"
```

---

### Task 6: discover / add_job: github source, progress lines, cross-source skip

**Files:**
- Modify: `pipeline/discover.py`, `pipeline/add_job.py` (full replacements below)
- Modify: `config/boards.json` (add the `github` key with a one-liner; keep everything else)
- Test: `tests/test_discover.py`, `tests/test_add_job.py` (full replacements below)

**Interfaces:**
- Consumes: `feeds.fetch_feed`, `progress.init` / `progress.progress`, `filters.title_key`.
- Produces: `discover.SOURCES` (FETCHERS plus `github`), `discover._recent_titles(con, days=3)`, `RECENT_TITLE_DAYS = 3`. Progress lines: `fetching N boards (...)`, `[ i/N] src/label  K fresh` or `dead`, `! src/label: Err: msg`, `filtering N jobs`, `S already appended · descriptions fetched: D`, `kept K · dropped ...`, and `appended N to Pipeline Jobs (dup link a, dup content b)` (`would append` in a dry run).

- [ ] **Step 1: Replace the two test files**

`tests/test_discover.py`:

```python
import json
import os
import subprocess
import sys

import pytest

from add_job import _db
from discover import count_today, finalize, format_yoe, init_rejected, run_filters
from enrich import init_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def con():
    c = _db(":memory:")
    init_cache(c)
    init_rejected(c)
    return c


def job(link, role="Software Engineer", company="Acme", location="New York, NY",
        description="3+ years of experience with Python.", **kw):
    j = {"job_link": link, "role": role, "company": company, "location": location,
         "country": "", "description": description, "detail_url": "", "ats": "greenhouse",
         "source": "greenhouse/acme", "extra_note": "", "date_posted": "2026-10-15"}
    j.update(kw)
    return j


def no_fetch(j):
    raise AssertionError("unexpected fetch")


def test_run_filters_keeps_rejects_and_records(con, prof):
    jobs = [job("https://x/1"), job("https://x/2", role="Engineering Manager"),
            job("https://x/3", description="8+ years of experience.")]
    kept, rejected, skipped = run_filters(jobs, prof, con, "test", fetch=no_fetch)
    assert [j["job_link"] for j in kept] == ["https://x/1"]
    assert sorted(r["reason"] for r in rejected) == ["family", "yoe"]
    assert skipped == 0
    rows = dict(con.execute("SELECT url_key, reason FROM rejected"))
    assert rows == {"https://x/2": "family", "https://x/3": "yoe"}


def test_already_appended_jobs_are_skipped(con, prof):
    con.execute("INSERT INTO seen VALUES ('https://x/1','k','Acme','SE','NY','https://x/1',"
                "'test','source: greenhouse/acme','2026-10-15T01:00:00')")
    kept, rejected, skipped = run_filters([job("https://x/1")], prof, con, "test", fetch=no_fetch)
    assert (kept, rejected, skipped) == ([], [], 1)


def test_merged_rows_stay_hidden_next_run(con, prof):
    first = [job("https://x/a", location="Austin, TX"), job("https://x/b", location="Boston, MA")]
    kept, rejected, _ = run_filters(first, prof, con, "test", fetch=no_fetch)
    assert len(kept) == 1 and [r["reason"] for r in rejected] == ["merged"]
    loser = rejected[0]["job_link"]
    _, _, skipped = run_filters([job(loser)], prof, con, "test", fetch=no_fetch)
    assert skipped == 1


def test_cap_uses_rows_appended_today(con, prof):
    for i in range(3):
        con.execute("INSERT INTO seen VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"https://old/{i}", f"k{i}", "Acme", f"r{i}", "NY", "", "test", "",
                     "2026-10-15T01:00:00"))
    kept, rejected, _ = run_filters([job("https://x/new")], prof, con, "test",
                                    fetch=no_fetch, today="2026-10-15")
    assert kept == [] and rejected[0]["reason"] == "cap"
    assert count_today(con, "2026-10-15") == {"acme": 3}
    assert count_today(con, "2026-10-16") == {}


def test_rejected_log_failure_does_not_block(prof):
    c = _db(":memory:")
    init_cache(c)  # no rejected table: the audit write fails
    kept, rejected, _ = run_filters([job("https://x/1"), job("https://x/2", role="Software Engineer Intern")],
                                    prof, c, "test", fetch=no_fetch)
    assert [j["job_link"] for j in kept] == ["https://x/1"]
    assert [r["reason"] for r in rejected] == ["level"]


def test_finalize_formats_sheet_fields():
    j = finalize({"source": "greenhouse/acme", "score": 78, "yoe_min": 3, "yoe_max": 5,
                  "sponsorship": "unknown", "stack_terms": ["Python", "React"],
                  "tags": ["loc ?"], "extra_note": "date=updated_at, spot-check"})
    assert j["yoe"] == "3–5"
    assert j["stack"] == "Python, React"
    assert j["notes"] == ("source: greenhouse/acme; score 78; yoe 3–5; sponsor ?; loc ?; "
                          "date=updated_at, spot-check")
    assert format_yoe(5, None) == "5+" and format_yoe(None, None) == ""


def test_bad_profile_exits_before_fetching(tmp_path):
    bad = tmp_path / "profile.json"
    bad.write_text("{}")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "pipeline", "discover.py"),
                        "--agent", "test", "--sources", "greenhouse",
                        "--profile", str(bad), "--db", str(tmp_path / "s.db"),
                        "--out-dir", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode != 0
    assert "missing keys" in r.stderr
    assert not list(tmp_path.glob("candidates_*"))


def test_same_company_and_title_recently_appended_is_skipped(con, prof):
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT INTO seen VALUES ('https://boards/1','k','Acme, Inc.','Software Engineer',"
                "'NY','https://boards/1','claude','source: greenhouse/acme',?)", (now,))
    feed_copy = job("https://jobright.ai/jobs/info/abc", company="Acme", role="Software Engineer",
                    location="New York, NY, United States", source="github/jobright-swe-newgrad")
    kept, rejected, skipped = run_filters([feed_copy], prof, con, "feeds", fetch=no_fetch)
    assert (kept, rejected, skipped) == ([], [], 1)


def test_progress_lines(tmp_path, monkeypatch, con, prof):
    import discover
    import progress
    log = tmp_path / "progress.log"
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(log))
    progress.init("test")
    monkeypatch.setitem(discover.SOURCES, "github", lambda entry, hours: [job("https://x/1"), job("https://x/2", role="Intern")])
    jobs, dead, errors = discover.fetch_all({"github": [{"name": "feed-a", "format": "simplify", "url": "u"}]},
                                            ["github", "nope"], 30)
    run_filters(jobs, prof, con, "test", fetch=no_fetch)
    lines = log.read_text().splitlines()
    assert lines[0] == "fetching 1 boards (github 1)"
    assert lines[1].startswith("[ 1/1] github/feed-a") and lines[1].endswith("  2 fresh")
    assert lines[-2:] == ["0 already appended · descriptions fetched: 0", "kept 1 · dropped family 1"]
    assert errors == ["unknown source: nope"]
```

`tests/test_add_job.py`:

```python
import json
import os
import sqlite3
import subprocess
import sys

from add_job import RUNS_HEADER, dropped_summary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADD_JOB = os.path.join(ROOT, "pipeline", "add_job.py")


def test_dropped_summary_reads_sibling_file(tmp_path):
    (tmp_path / "candidates_test_1.json").write_text("[]")
    (tmp_path / "rejected_test_1.json").write_text(json.dumps(
        [{"reason": "level"}, {"reason": "level"}, {"reason": "yoe"}]))
    assert dropped_summary(str(tmp_path / "candidates_test_1.json")) == "level 2, yoe 1"


def test_dropped_summary_without_sibling(tmp_path):
    (tmp_path / "candidates_test_2.json").write_text("[]")
    assert dropped_summary(str(tmp_path / "candidates_test_2.json")) == "-"
    assert dropped_summary(str(tmp_path / "other.json")) == "-"


def test_runs_header_has_dropped():
    assert RUNS_HEADER[-1] == "Dropped" and len(RUNS_HEADER) == 9


def _run(tmp_path, rows):
    inp = tmp_path / "candidates_test_x.json"
    inp.write_text(json.dumps(rows))
    db = tmp_path / "seen.db"
    r = subprocess.run([sys.executable, ADD_JOB, "--agent", "test", "--input", str(inp),
                        "--skip-sheet", "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), sqlite3.connect(db)


def test_old_format_candidates_still_work(tmp_path):
    old = [{"date_posted": "2026-10-14", "company": "Datadog", "role": "Software Engineer",
            "location": "New York, NY", "yoe": "", "job_link": "https://x/1", "stack": "",
            "notes": "source: greenhouse/datadog"}]
    summary, _ = _run(tmp_path, old)
    assert summary["appended"] == 1


def test_appends_best_score_first(tmp_path):
    rows = [{"company": c, "role": "Software Engineer", "location": "NY",
             "job_link": f"https://x/{c}", "notes": "source: t", "score": s}
            for c, s in (("low", 10), ("high", 90), ("mid", 50))]
    _, con = _run(tmp_path, rows)
    order = [r[0] for r in con.execute("SELECT company FROM seen ORDER BY rowid")]
    assert order == ["high", "mid", "low"]


def test_add_job_writes_a_progress_line(tmp_path):
    rows = [{"company": "Acme", "role": "Software Engineer", "location": "NY",
             "job_link": "https://x/a", "notes": "source: t"}]
    inp = tmp_path / "candidates_test_y.json"
    inp.write_text(json.dumps(rows))
    log = tmp_path / "progress.log"
    env = dict(os.environ, JOB_PROGRESS_FILE=str(log))
    r = subprocess.run([sys.executable, ADD_JOB, "--agent", "test", "--input", str(inp), "--dry-run"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert log.read_text() == "would append 1 to Pipeline Jobs (dup link 0, dup content 0)\n"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q`
Expected: `3 failed, 278 passed` (the same-title skip, the discover progress lines, and the add_job progress line).

- [ ] **Step 3: Replace `pipeline/discover.py`**

```python
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
RECENT_TITLE_DAYS = 3

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
        fetched[0] += 1
        return fetch(j)
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
    ap.add_argument("--hours", type=int, default=30,
                    help="lookback window; 30h gives overlap between 6h runs (dedup handles it)")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "boards.json"))
    ap.add_argument("--profile", default=os.path.join(ROOT, "config", "profile.json"))
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "runs"))
    args = ap.parse_args()
    progress_init(args.agent)

    try:
        prof = load_profile(args.profile)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    cfg = load_config(args.config)
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
```

- [ ] **Step 4: Replace `pipeline/add_job.py`**

```python
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import init as progress_init, progress  # noqa: E402

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
    progress_init(args.agent)

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

    verb = "would append" if args.dry_run else "appended"
    progress(f"{verb} {summary['appended']} to {JOBS_TAB} "
             f"(dup link {summary['dup_url']}, dup content {summary['dup_content']})")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Add the feeds to `config/boards.json`**

Run: `.venv/bin/python -c "import json;p='config/boards.json';d=json.load(open(p));d['github']=[{'name':'simplify-newgrad','format':'simplify','url':'https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json'},{'name':'speedyapply-newgrad-usa','format':'speedyapply','url':'https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md'},{'name':'jobright-swe-newgrad','format':'jobright','url':'https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/master/README.md'}];json.dump(d,open(p,'w'),indent=1);print([e['name'] for e in d['github']], sorted(d))"`
Expected: `['simplify-newgrad', 'speedyapply-newgrad-usa', 'jobright-swe-newgrad'] ['_comment', 'ashby', 'github', 'greenhouse', 'lever', 'smartrecruiters', 'workday']`

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `281 passed`.

- [ ] **Step 7: Commit**

```bash
git add pipeline/discover.py pipeline/add_job.py config/boards.json tests/test_discover.py tests/test_add_job.py
git commit -m "feat(discover): github feeds source, progress lines, cross-source same-title skip"
```

---

### Task 7: The runner

**Files:**
- Create: `pipeline/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `progress.default_path`; `discover.py` / `add_job.py` CLIs (`--db`, `--out-dir`, `--dry-run`); the `seen` table.
- Produces: CLI `runner.py --agent A [--label L] [--stdin F] [--stream claude|lines] [--retries N] [--retry-wait S] [--direct SOURCES] [--dry-run] [--db P] [--logs-dir D] [--runs-dir D] -- COMMAND...` and `runner.py --total a,b,c`. It writes `logs/<agent>-<YYYYMMDD-HHMM>.log` and `logs/last-run-<agent>.json`; `JOB_NOTIFY=0` silences lane notifications; `JOB_RUN_STARTED` bounds `--total`.

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py`:

```python
import json
import os
import re
import sqlite3
import sys
import textwrap
import time

import pytest

import runner
from runner import (candidates_since, extract_report, first_line, fmt_duration,
                    notify, parse_claude_event)


def test_parse_claude_event_kinds():
    tool = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "thinking"},
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "python3 pipeline/discover.py --agent claude", "description": "Run discovery"}},
        {"type": "text", "text": "Discovery done.\nDetails follow."}]}})
    assert parse_claude_event(tool) == [("tool", "Bash: Run discovery"),
                                        ("say", "Discovery done.\nDetails follow.")]
    denied = json.dumps({"type": "system", "subtype": "permission_denied", "tool_name": "Bash"})
    assert parse_claude_event(denied) == [("denied", "Bash")]
    result = json.dumps({"type": "result", "subtype": "success", "result": "**Report**", "is_error": False})
    assert parse_claude_event(result) == [("result", "**Report**")]
    hook = json.dumps({"type": "system", "subtype": "hook_started"})
    assert parse_claude_event(hook) == []
    assert parse_claude_event("Traceback (most recent call last):\n") == [
        ("raw", "Traceback (most recent call last):")]


def test_first_line_and_duration():
    assert first_line("\n  hello   world \nsecond") == "hello world"
    assert first_line("x" * 200, width=10) == "x" * 9 + "…"
    assert [fmt_duration(s) for s in (42, 312, 3780)] == ["42s", "5m 12s", "1h 03m"]


def test_extract_report():
    lines = ["I am launching discovery.", "Waiting.", "### Run Summary", "* Fetched: 3"]
    assert extract_report(lines) == ["### Run Summary", "* Fetched: 3"]
    assert extract_report(["a"] * 40) == ["a"] * 30


def test_notify_respects_job_notify(monkeypatch):
    calls = []
    monkeypatch.setenv("JOB_NOTIFY", "0")
    notify("T", "m", run=lambda *a, **k: calls.append(a[0]))
    assert calls == []
    notify("T", "m", force=True, run=lambda *a, **k: calls.append(a[0]))
    assert calls[0][0] == "osascript" and calls[0][-2:] == ["T", "m"]


def test_candidates_since_ignores_older_files(tmp_path):
    old = tmp_path / "candidates_claude_old.json"
    old.write_text(json.dumps([{"job_link": "a", "score": 1}]))
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (tmp_path / "candidates_claude_new.json").write_text(json.dumps([{"job_link": "b", "score": 9}]))
    (tmp_path / "candidates_gemini_new.json").write_text(json.dumps([{"job_link": "c"}]))
    assert list(candidates_since(str(tmp_path), "claude", time.time() - 60)) == ["b"]


FAKE_AGENT = textwrap.dedent('''
    import os, sqlite3, sys, json, time
    from datetime import datetime
    tries = sys.argv[1]
    n = int(open(tries).read()) if os.path.exists(tries) else 0
    open(tries, "w").write(str(n + 1))
    if n < int(sys.argv[3]):
        print("API error 503", file=sys.stderr)
        sys.exit(1)
    with open(os.environ["JOB_PROGRESS_FILE"], "a") as f:
        f.write("[ 1/1] greenhouse/acme   2 fresh\\n")
        f.write("! greenhouse/broken: HTTPError: 404\\n")
    print("I am running discovery.", flush=True)
    time.sleep(0.5)
    con = sqlite3.connect(sys.argv[2])
    con.execute("INSERT INTO seen VALUES ('u1','k1','Acme','Software Engineer','NY','https://x/1',"
                "'fake','source: t',?)", (datetime.now().isoformat(timespec="seconds"),))
    con.commit()
    runs = os.path.join(os.path.dirname(sys.argv[2]), "runs")
    os.makedirs(runs, exist_ok=True)
    json.dump([{"job_link": "https://x/1", "score": 42, "yoe": "3+"}],
              open(os.path.join(runs, "candidates_fake_1.json"), "w"))
    print("### Run report")
    print("* appended 1")
''')


@pytest.fixture
def lane(tmp_path):
    db = tmp_path / "seen.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE seen(url_key TEXT PRIMARY KEY, content_key TEXT UNIQUE,
        company TEXT, role TEXT, location TEXT, job_link TEXT, agent TEXT, source TEXT, first_seen TEXT)""")
    con.commit()
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT)
    return tmp_path, db, agent


def _args(tmp, db, agent, fails=0, retries=1):
    return ["--agent", "fake", "--label", "Test lane", "--stream", "lines",
            "--retries", str(retries), "--retry-wait", "0",
            "--db", str(db), "--logs-dir", str(tmp / "logs"), "--runs-dir", str(tmp / "runs"),
            "--", sys.executable, str(agent), str(tmp / "tries"), str(db), str(fails)]


def test_lane_shows_progress_narration_and_summary(lane, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    tmp, db, agent = lane
    assert runner.main(_args(tmp, db, agent)) == 0
    out = capsys.readouterr().out
    assert "━━ Fake · Test lane · started" in out
    assert "  · [ 1/1] greenhouse/acme   2 fresh" in out
    assert "  ! greenhouse/broken: HTTPError: 404" in out
    assert "  › I am running discovery." in out
    assert "━━ Fake done ·" in out and "1 job added" in out
    assert " 42  Acme" in out and "3+" in out and "https://x/1" in out
    assert "  Agent report:\n    ### Run report\n    * appended 1" in out
    assert "  › ### Run report" not in out          # report isn't echoed twice
    log = next((tmp / "logs").glob("fake-*.log"))
    assert f"  View: less +G {log}" in out
    assert "I am running discovery." in log.read_text()
    last = json.loads((tmp / "logs" / "last-run-fake.json").read_text())
    assert (last["added"], last["exit"]) == (1, 0)


def test_lane_retries_then_succeeds(lane, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    tmp, db, agent = lane
    assert runner.main(_args(tmp, db, agent, fails=1, retries=3)) == 0
    out = capsys.readouterr().out
    assert "  ! attempt 1 failed (exit 1), retrying in 0s" in out
    assert "1 job added" in out
    assert "API error 503" in next((tmp / "logs").glob("fake-*.log")).read_text()


def test_lane_gives_up_after_retries(lane, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    tmp, db, agent = lane
    assert runner.main(_args(tmp, db, agent, fails=5, retries=2)) == 1
    out = capsys.readouterr().out
    assert "0 jobs added" in out and "  ! exited with code 1" in out


def test_missing_command(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    code = runner.main(["--agent", "x", "--logs-dir", str(tmp_path), "--runs-dir", str(tmp_path),
                        "--db", str(tmp_path / "none.db"), "--", "no-such-cli-xyz"])
    assert code == 127
    assert "  ! command not found: no-such-cli-xyz" in capsys.readouterr().out


def test_total_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JOB_RUN_STARTED", str(time.time() - 5))
    sent = []
    monkeypatch.setattr(runner, "notify", lambda t, m, force=False: sent.append((t, m, force)))
    (tmp_path / "last-run-claude.json").write_text(json.dumps({"added": 1, "exit": 0}))
    (tmp_path / "last-run-gemini.json").write_text(json.dumps({"added": 2, "exit": 1}))
    runner.main(["--total", "claude,gemini,feeds", "--logs-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert re.search(r"━━ Total: 3 jobs added \(claude 1, gemini 2!, feeds –\) · \d+s ━━", out)
    assert sent == [("Job pipeline", "3 jobs added (claude 1, gemini 2!, feeds –)", True)]


def test_direct_mode_runs_discover_then_add_job(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    runs = tmp_path / "runs"
    runs.mkdir()
    cand = runs / "candidates_feeds_1.json"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1].endswith("discover.py"):
            cand.write_text(json.dumps([{"job_link": "https://x/9", "company": "WPP",
                                         "role": "Software Engineer", "score": 61, "yoe": "2+"}]))
            out = {"candidates_file": str(cand), "fetched": 70, "already_known": 5, "kept": 1,
                   "errors": ["github/jobright-swe-newgrad: HTTPError: 503"]}
        else:
            out = {"appended": 1}
        return runner.subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    code = runner.main(["--agent", "feeds", "--label", "GitHub feeds", "--direct", "github",
                        "--dry-run", "--db", str(tmp_path / "s.db"),
                        "--logs-dir", str(tmp_path / "logs"), "--runs-dir", str(runs)])
    assert code == 0
    assert calls[0][-6:] == ["--sources", "github", "--db", str(tmp_path / "s.db"), "--out-dir", str(runs)]
    assert calls[1][-1] == "--dry-run" and str(cand) in calls[1]
    out = capsys.readouterr().out
    assert "━━ Feeds done ·" in out and "1 job to add (dry run)" in out
    assert " 61  WPP" in out
    assert "  Run report:\n    fetched 70 · already appended 5 · kept 1 · would append 1" in out
    assert "    ! github/jobright-swe-newgrad: HTTPError: 503" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q`
Expected: `1 error` (`No module named 'runner'`).

- [ ] **Step 3: Create `pipeline/runner.py`**

```python
"""Run one pipeline lane with live progress and an end-of-lane summary.

    runner.py --agent claude --label "..." --stdin prompts/claude.md --stream claude -- claude -p ...
    runner.py --agent gemini --label "..." --stream lines --retries 3 --retry-wait 120 -- agy -p ...
    runner.py --agent feeds --label "GitHub feeds" --direct github [--db PATH] [--dry-run]
    runner.py --total claude,gemini,feeds

Two live streams reach the terminal: progress lines that pipeline code
writes through progress.py ("  · ..."), and the agent's own narration
("  › ..."). The summary lists what this lane appended, read from the seen
registry rather than from the agent's words.
"""
import argparse
import glob
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime

PIPE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PIPE)
sys.path.insert(0, PIPE)
from progress import default_path  # noqa: E402

LOGS = os.path.join(ROOT, "logs")
RUNS = os.path.join(ROOT, "runs")
DB = os.path.join(PIPE, "seen.db")
BAR = "━━"
WIDTH = 110
REPORT_LINES = 40
TABLE_ROWS = 25


# --------------------------------------------------------------- formatting
def first_line(text, width=WIDTH):
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line if len(line) <= width else line[:width - 1] + "…"
    return ""


def fmt_duration(seconds):
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def fmt_job(j):
    score = j.get("score")
    return (f"  {score if score is not None else '':>3}  {j['company'][:14]:<14} "
            f"{j['role'][:44]:<44} {(j.get('yoe') or '?'):<5} {j['job_link']}")


def _say(text):
    if text:
        print(f"  › {text}", flush=True)


# ------------------------------------------------------------ agent output
def parse_claude_event(line):
    """One Claude CLI stream-json line -> [(kind, text)].
    kinds: say, tool, denied, result, error, raw (non-JSON line)."""
    try:
        e = json.loads(line)
    except ValueError:
        return [("raw", line.rstrip("\n"))] if line.strip() else []
    out, kind = [], e.get("type")
    if kind == "assistant":
        for c in (e.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and (c.get("text") or "").strip():
                out.append(("say", c["text"]))
            elif c.get("type") == "tool_use":
                inp = c.get("input") or {}
                what = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
                out.append(("tool", f"{c.get('name')}: {what}"))
    elif kind == "system" and e.get("subtype") == "permission_denied":
        out.append(("denied", e.get("tool_name") or "tool"))
    elif kind == "result":
        out.append(("result", e.get("result") or ""))
        if e.get("is_error"):
            out.append(("error", e.get("subtype") or "error"))
    return out


def _report_start(line):
    s = line.lstrip()
    return s.startswith("#") or s.startswith("|")


def extract_report(lines):
    """Plain-text agents: the report runs from the first Markdown heading or
    table to the end; without one, the last 30 lines."""
    for i, line in enumerate(lines):
        if _report_start(line):
            return lines[i:]
    return lines[-30:]


def _stream(proc, stream, log, bangs):
    """Echo the agent's stdout live; return its report lines."""
    report, plain, in_report = [], [], False
    for line in proc.stdout:
        if stream == "claude":
            for kind, text in parse_claude_event(line):
                if kind == "say":
                    _say(first_line(text))
                    log.write(text + "\n")
                elif kind == "tool":
                    _say(first_line(text))
                    log.write(f"> {text}\n")
                elif kind == "denied":
                    bangs.append(f"permission denied: {text}")
                    print(f"  ! permission denied: {text}", flush=True)
                    log.write(f"! permission denied: {text}\n")
                elif kind == "result":
                    report = text.splitlines()
                    log.write(text + "\n")
                elif kind == "error":
                    bangs.append(f"agent reported an error: {text}")
                else:
                    log.write(text + "\n")
        else:
            log.write(line)
            text = line.rstrip("\n")
            plain.append(text)
            in_report = in_report or _report_start(text)
            if not in_report:
                _say(first_line(text))
        log.flush()
    return report if stream == "claude" else extract_report(plain)


def run_agent(a, env, log, bangs):
    """Start the agent CLI (with retries). Returns (exit code, report lines)."""
    report = []
    for attempt in range(1, a.retries + 1):
        stdin = open(a.stdin) if a.stdin else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(a.command, stdin=stdin, stdout=subprocess.PIPE,
                                    stderr=log, text=True, env=env, cwd=ROOT)
        except FileNotFoundError:
            bangs.append(f"command not found: {a.command[0]}")
            return 127, report
        finally:
            if a.stdin:
                stdin.close()
        report = _stream(proc, a.stream, log, bangs)
        code = proc.wait()
        if code == 0 or attempt == a.retries:
            return code, report
        print(f"  ! attempt {attempt} failed (exit {code}), retrying in {a.retry_wait}s", flush=True)
        log.write(f"--- attempt {attempt} failed (exit {code}) ---\n")
        time.sleep(a.retry_wait)
    return 1, report


def run_direct(a, env, log, bangs):
    """No LLM: discover.py, then add_job.py on the candidates it wrote."""
    py = sys.executable
    cmd = [py, os.path.join(PIPE, "discover.py"), "--agent", a.agent,
           "--sources", a.direct, "--db", a.db, "--out-dir", a.runs_dir]
    d = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    log.write(d.stdout + d.stderr)
    if d.returncode != 0:
        bangs.append(first_line(d.stderr.strip().splitlines()[-1] if d.stderr.strip() else "discover failed"))
        return d.returncode, [], None
    found = json.loads(d.stdout)
    cmd = [py, os.path.join(PIPE, "add_job.py"), "--agent", a.agent,
           "--input", found["candidates_file"], "--db", a.db] + (["--dry-run"] if a.dry_run else [])
    g = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    log.write(g.stdout + g.stderr)
    if g.returncode != 0:
        bangs.append(first_line(g.stderr.strip().splitlines()[-1] if g.stderr.strip() else "add_job failed"))
        return g.returncode, [], found
    added = json.loads(g.stdout)
    report = [f"fetched {found['fetched']} · already appended {found['already_known']} · "
              f"kept {found['kept']} · {'would append' if a.dry_run else 'appended'} {added['appended']}"]
    report += [f"! {e}" for e in found.get("errors", [])]
    return 0, report, found


# ---------------------------------------------------------------- progress
def tail_progress(path, stop, bangs):
    """Print lines appended to the progress file until stop is set."""
    pos = 0
    while True:
        done = stop.is_set()
        try:
            with open(path) as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
        except OSError:
            chunk = ""
        for line in chunk.splitlines():
            if line.startswith("!"):
                bangs.append(line[1:].strip())
                print(f"  {line}", flush=True)
            else:
                print(f"  · {line}", flush=True)
        if done:
            return
        stop.wait(0.3)


# ----------------------------------------------------------------- summary
def appended_since(db, agent, since_iso):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT company, role, location, job_link FROM seen "
                           "WHERE agent = ? AND first_seen >= ? ORDER BY rowid",
                           (agent, since_iso)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return [dict(zip(("company", "role", "location", "job_link"), r)) for r in rows]


def candidates_since(runs_dir, agent, since_ts):
    """job_link -> candidate dict, from this agent's candidates files of this lane."""
    out = {}
    for path in sorted(glob.glob(os.path.join(runs_dir, f"candidates_{agent}_*.json"))):
        if os.path.getmtime(path) < since_ts:
            continue
        try:
            with open(path) as f:
                for c in json.load(f):
                    out[c.get("job_link")] = c
        except (OSError, ValueError):
            continue
    return out


def notify(title, message, force=False, run=subprocess.run):
    """macOS banner; silent when JOB_NOTIFY=0 (unless force) or osascript is missing."""
    if os.environ.get("JOB_NOTIFY") == "0" and not force:
        return
    try:
        run(["osascript", "-e", "on run argv", "-e",
             "display notification (item 2 of argv) with title (item 1 of argv)",
             "-e", "end run", title, message], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _plural(n):
    return f"{n} job{'s' if n != 1 else ''}"


def print_summary(a, code, report, bangs, jobs, seconds, log_path):
    name = a.agent.capitalize()
    verb = "to add (dry run)" if a.dry_run else "added"
    print(f"{BAR} {name} done · {fmt_duration(seconds)} · {_plural(len(jobs))} {verb} {BAR}")
    ranked = sorted(jobs, key=lambda j: -(j.get("score") or 0))
    for j in ranked[:TABLE_ROWS]:
        print(fmt_job(j))
    if len(ranked) > TABLE_ROWS:
        print(f"  … {len(ranked) - TABLE_ROWS} more (all in the sheet, best first)")
    if code != 0:
        print(f"  ! exited with code {code}")
    for b in bangs[:5]:
        print(f"  ! {b}")
    if len(bangs) > 5:
        print(f"  ! … {len(bangs) - 5} more in the log")
    if report:
        print("  Agent report:" if not a.direct else "  Run report:")
        for line in report[:REPORT_LINES]:
            print(f"    {line}")
        if len(report) > REPORT_LINES:
            print(f"    … {len(report) - REPORT_LINES} more lines in the log")
    print(f"  Log:  {log_path}")
    print(f"  View: less +G {log_path}", flush=True)


def run_lane(a):
    started, t0 = datetime.now(), time.time()
    os.makedirs(a.logs_dir, exist_ok=True)
    log_path = os.path.join(a.logs_dir, f"{a.agent}-{started:%Y%m%d-%H%M}.log")
    prog_path = (default_path(a.agent) if a.logs_dir == LOGS
                 else os.path.join(a.logs_dir, f"progress-{a.agent}.log"))
    open(prog_path, "w").close()
    env = dict(os.environ, JOB_PROGRESS_FILE=prog_path, PYTHONUNBUFFERED="1")
    print(f"{BAR} {a.agent.capitalize()} · {a.label} · started {started:%H:%M} {BAR}", flush=True)

    bangs, stop = [], threading.Event()
    tail = threading.Thread(target=tail_progress, args=(prog_path, stop, bangs), daemon=True)
    tail.start()
    found = None
    with open(log_path, "a") as log:
        if a.direct:
            code, report, found = run_direct(a, env, log, bangs)
        else:
            code, report = run_agent(a, env, log, bangs)
    stop.set()
    tail.join(timeout=5)

    seconds = time.time() - t0
    cands = candidates_since(a.runs_dir, a.agent, t0)
    if a.dry_run:
        jobs = list(cands.values())
    else:
        jobs = [dict(j, **{k: cands.get(j["job_link"], {}).get(k) for k in ("score", "yoe")})
                for j in appended_since(a.db, a.agent, started.isoformat(timespec="seconds"))]
    print_summary(a, code, report, bangs, jobs, seconds, log_path)

    with open(os.path.join(a.logs_dir, f"last-run-{a.agent}.json"), "w") as f:
        json.dump({"agent": a.agent, "added": len(jobs), "exit": code, "seconds": int(seconds),
                   "jobs": [{k: j.get(k) for k in ("company", "role", "job_link")} for j in jobs]}, f)
    companies = ", ".join(dict.fromkeys(j["company"] for j in jobs))
    notify(f"Job pipeline · {a.agent.capitalize()}",
           f"{_plural(len(jobs))} added" + (f": {companies}" if companies else "")
           + (f" (exit {code})" if code else ""))
    return code


def run_total(a):
    started = float(os.environ.get("JOB_RUN_STARTED") or 0)
    parts, total = [], 0
    for agent in [x for x in a.total.split(",") if x]:
        path = os.path.join(a.logs_dir, f"last-run-{agent}.json")
        try:
            if os.path.getmtime(path) < started:
                raise OSError("stale")
            with open(path) as f:
                r = json.load(f)
        except (OSError, ValueError):
            parts.append(f"{agent} –")
            continue
        total += r.get("added", 0)
        parts.append(f"{agent} {r.get('added', 0)}" + ("!" if r.get("exit") else ""))
    took = f" · {fmt_duration(time.time() - started)}" if started else ""
    print(f"{BAR} Total: {_plural(total)} added ({', '.join(parts)}){took} {BAR}", flush=True)
    notify("Job pipeline", f"{_plural(total)} added ({', '.join(parts)})", force=True)
    return 0


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--agent")
    ap.add_argument("--label", default="")
    ap.add_argument("--stdin", help="file fed to the agent's stdin (the prompt)")
    ap.add_argument("--stream", choices=["claude", "lines"], default="lines")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--retry-wait", type=int, default=120)
    ap.add_argument("--direct", help="sources for a no-LLM discover + add_job run")
    ap.add_argument("--dry-run", action="store_true", help="--direct only: add_job --dry-run")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--logs-dir", default=LOGS)
    ap.add_argument("--runs-dir", default=RUNS)
    ap.add_argument("--total", help="comma-separated agents: print the combined total")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.command and a.command[0] == "--":
        a.command = a.command[1:]
    if not a.total and not a.agent:
        ap.error("--agent is required")
    if not (a.total or a.direct or a.command):
        ap.error("give a command after --, or --direct SOURCES")
    return a


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    return run_total(a) if a.total else run_lane(a)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `292 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner.py
git commit -m "feat(runner): live progress, end-of-lane summary, notification, total"
```

---

### Task 8: Scripts, README, alias

**Files:**
- Modify: `run_claude.sh`, `run_gemini.sh` (full replacements)
- Create: `run_feeds.sh`, `run_all.sh`
- Modify: `README.md` (one new section), `~/.zshrc` (one alias line, only if absent)

- [ ] **Step 1: Replace `run_claude.sh`**

```bash
#!/bin/bash
# Claude lane: Greenhouse + Ashby + Lever. Live progress and a summary via pipeline/runner.py.
# Allow rules are passed as flags: .claude/settings.json is ignored in an untrusted workspace.
# The prompt goes in on stdin (runner --stdin) so the variadic --allowedTools can't swallow it.
set -euo pipefail
cd "$(dirname "$0")"
# Use the venv so `python3` has gspread etc. (system python3 doesn't).
export PATH="$PWD/.venv/bin:$PATH"
exec python3 pipeline/runner.py --agent claude --label "Greenhouse · Ashby · Lever" \
  --stdin prompts/claude.md --stream claude -- \
  claude -p --verbose --output-format stream-json --permission-mode acceptEdits \
  --allowedTools "Bash(python3 pipeline/*)" "Bash(python3 -c:*)" "Read" "Edit(pipeline/fetchers.py)" "Edit(config/boards.json)"
```

- [ ] **Step 2: Replace `run_gemini.sh`**

```bash
#!/bin/bash
# Gemini lane (Antigravity agy): Workday + SmartRecruiters. Live progress and a summary via
# pipeline/runner.py. Auth: the cached account login (run `agy` interactively once to sign in).
set -euo pipefail
cd "$(dirname "$0")"
# Use the venv so `python3` has gspread etc. (system python3 doesn't).
export PATH="$HOME/.local/bin:$PWD/.venv/bin:$PATH"
# Headless soft-denies any shell command not in permissions.allow, and the agent
# improvises compound commands no rule list can predict, so approve all tools.
# Guardrail is the prompt: append-only, sheet writes only via add_job.py.
# Transient API errors (e.g. 503 high demand): the runner retries the whole run.
exec python3 pipeline/runner.py --agent gemini --label "Workday · SmartRecruiters" \
  --stream lines --retries 3 --retry-wait 120 -- \
  agy -p "$(cat prompts/gemini.md)" --dangerously-skip-permissions --print-timeout 20m
```

- [ ] **Step 3: Create `run_feeds.sh`**

```bash
#!/bin/bash
# GitHub feeds lane (SimplifyJobs, speedyapply, jobright): plain discover + add_job, no LLM.
# Extra flags pass through to the runner, e.g. ./run_feeds.sh --dry-run --db /tmp/seen-copy.db
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
exec python3 pipeline/runner.py --agent feeds --label "GitHub feeds" --direct github "$@"
```

- [ ] **Step 4: Create `run_all.sh`**

```bash
#!/bin/bash
# Every lane, one command (alias: jobhunt): Claude, Gemini, GitHub feeds, then a total.
# A failing lane doesn't stop the next one. One macOS notification at the end.
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
export JOB_NOTIFY=0 JOB_RUN_STARTED="$(date +%s)"
./run_claude.sh || true
./run_gemini.sh || true
./run_feeds.sh || true
python3 pipeline/runner.py --total claude,gemini,feeds
```

- [ ] **Step 5: Make them executable and check syntax**

Run: `chmod +x run_claude.sh run_gemini.sh run_feeds.sh run_all.sh && for f in run_*.sh; do bash -n "$f" && echo "ok $f"; done`
Expected: four `ok` lines.

- [ ] **Step 6: README: add this section right after the `## How dedup works` heading's section (before `## Monitoring`)**

```markdown
## Running it (`jobhunt`)

One command runs every lane from any folder: `jobhunt` (alias for `run_all.sh`).

1. Claude lane (Greenhouse, Ashby, Lever), then Gemini lane (Workday, SmartRecruiters),
   then the GitHub feeds lane (SimplifyJobs, speedyapply, jobright; no LLM).
2. While a lane runs: `·` lines come from the pipeline (per board, filter, append),
   `›` lines are the agent's narration.
3. After each lane: the jobs it appended (score, company, role, YOE, link), the agent's
   report, and the log path with a ready `less +G <log>` command.
4. At the end: one total line and one macOS notification.

Single lanes: `./run_claude.sh`, `./run_gemini.sh`, `./run_feeds.sh`.
Safe trial of the feeds lane (no sheet writes):
`cp pipeline/seen.db /tmp/seen-copy.db && ./run_feeds.sh --dry-run --db /tmp/seen-copy.db`
```

- [ ] **Step 7: Add the alias (once)**

Run: `grep -q 'alias jobhunt=' ~/.zshrc || echo 'alias jobhunt="$HOME/Work/job-pipeline/run_all.sh"' >> ~/.zshrc; grep -n 'alias jobhunt=' ~/.zshrc`
Expected: one line `alias jobhunt="$HOME/Work/job-pipeline/run_all.sh"`.

- [ ] **Step 8: Commit**

```bash
git add run_claude.sh run_gemini.sh run_feeds.sh run_all.sh README.md
git commit -m "feat: jobhunt — one command for every lane, thin lane scripts"
```

---

### Task 9: Verify end-to-end, then hand over

**Files:** none changed.

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `292 passed`.

- [ ] **Step 2: Feeds lane against live feeds, on a registry copy, no sheet writes**

Run: `cp pipeline/seen.db /tmp/seen-copy.db && JOB_NOTIFY=0 ./run_feeds.sh --dry-run --db /tmp/seen-copy.db`
Expected:
- a `━━ Feeds · GitHub feeds · started HH:MM ━━` header;
- three `· [ i/3] github/…  N fresh` lines;
- `· kept K · dropped …`;
- a `━━ Feeds done · … · K jobs to add (dry run) ━━` table of at most 25 rows;
- `Run report:`, `Log:` and `View: less +G …`.

The live prototype run had 287 fresh and 84 kept, and took about 3 minutes.

- [ ] **Step 3: Claude lane parses on a harmless prompt**

Run: `echo 'Reply with exactly: ok' | JOB_NOTIFY=0 .venv/bin/python pipeline/runner.py --agent probe --label "stream check" --stdin /dev/stdin --stream claude -- claude -p --verbose --output-format stream-json`
Expected: header, `› ok`, `━━ Probe done · … · 0 jobs added ━━`, `Agent report:` with `ok`.

- [ ] **Step 4: Show the human the Step 2 output; they run `jobhunt` themselves**

`jobhunt` writes to the real sheet. Hand over the command and the alias (open a new terminal or `source ~/.zshrc` first).
