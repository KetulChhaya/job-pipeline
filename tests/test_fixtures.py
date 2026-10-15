"""Labeled jobs: titles and locations are verbatim from the 2026-10-15 runs;
descriptions are short synthetic excerpts that carry the deciding phrase.
Company "Example Co" marks a fully synthetic case."""
import json
import os

import pytest

from enrich import enrich_job
from filters import full_check, quick_check, score

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "fixtures", "jobs.json")) as f:
    CASES = json.load(f)


def evaluate(job, prof):
    """quick_check -> enrich -> full_check, the same order discover uses."""
    job = dict(job, job_link=f"https://example.com/{job['id']}", detail_url="")
    c = quick_check(job, prof)
    if not c.keep:
        return c, job
    job["tags"] = list(c.tags)
    enrich_job(job, prof)
    c = full_check(job, prof)
    if c.keep:
        job["score"] = score(job, prof)
    return c, job


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fixture(prof, case):
    c, job = evaluate(case, prof)
    want = case["expect"]
    assert c.keep is want["keep"], (c, job.get("yoe_min"), job.get("sponsorship"))
    if not want["keep"]:
        assert c.reason == want["reason"], c
        return
    for tag in want.get("tags", []):
        assert tag in job["tags"]
    if "min_score" in want:
        assert job["score"] >= want["min_score"], job["score"]


def test_fixture_count():
    assert len(CASES) >= 30
