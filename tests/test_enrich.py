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
