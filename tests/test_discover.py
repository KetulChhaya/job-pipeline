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
