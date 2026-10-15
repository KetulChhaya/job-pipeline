"""Fetchers return every fresh posting (no role/location filtering) with the
fields filters.py and enrich.py need. HTTP is faked."""
from datetime import datetime, timezone

import pytest

import fetchers

NOW = datetime.now(timezone.utc)
ISO = NOW.isoformat()
MS = int(NOW.timestamp() * 1000)


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(fetchers.time, "sleep", lambda s: None)


def fake_get(monkeypatch, payload, seen=None):
    def get(url, params=None, **kw):
        if seen is not None:
            seen.append((url, params))
        return Resp(payload)
    monkeypatch.setattr(fetchers.requests, "get", get)


def test_greenhouse_keeps_all_titles_and_reads_content(monkeypatch):
    seen = []
    fake_get(monkeypatch, {"jobs": [
        {"id": 11, "title": "Product Designer", "first_published": ISO,
         "location": {"name": "London, UK"}, "absolute_url": "https://x/11",
         "company_name": "Acme", "content": "&lt;p&gt;3+ years of experience&lt;/p&gt;"},
    ]}, seen)
    [j] = fetchers.fetch_greenhouse("acme", hours=30)
    assert seen[0][1] == {"content": "true"}
    assert (j["role"], j["location"], j["ats"], j["job_id"]) == (
        "Product Designer", "London, UK", "greenhouse", "11")
    assert j["description"] == "3+ years of experience"
    assert j["source"] == "greenhouse/acme"
    assert j["notes"] == "source: greenhouse/acme"


def test_greenhouse_old_posting_still_skipped(monkeypatch):
    fake_get(monkeypatch, {"jobs": [{"id": 1, "title": "Software Engineer",
                                     "first_published": "2020-01-01T00:00:00Z",
                                     "location": {"name": "NYC"}}]})
    assert fetchers.fetch_greenhouse("acme", hours=30) == []


def test_lever_country_and_lists(monkeypatch):
    fake_get(monkeypatch, [{
        "id": "abc", "text": "Backend Engineer", "createdAt": MS, "country": "US",
        "categories": {"location": "Remote"}, "hostedUrl": "https://jobs.lever.co/x/abc",
        "descriptionPlain": "About us.", "additionalPlain": "Benefits.",
        "lists": [{"text": "Requirements", "content": "<li>4+ years of experience</li>"}],
    }])
    [j] = fetchers.fetch_lever("x", hours=30)
    assert (j["country"], j["ats"], j["job_id"]) == ("US", "lever", "abc")
    assert j["description"].splitlines() == ["About us.", "Requirements",
                                             "4+ years of experience", "Benefits."]


def test_ashby_country_and_description(monkeypatch):
    fake_get(monkeypatch, {"jobs": [{
        "id": "u1", "title": "AI Engineer", "publishedAt": ISO, "isListed": True,
        "location": "Remote", "isRemote": True, "jobUrl": "https://jobs.ashbyhq.com/x/u1",
        "address": {"postalAddress": {"addressCountry": "United States"}},
        "descriptionPlain": "2+ years of experience with LLMs.",
    }]})
    [j] = fetchers.fetch_ashby("x", hours=30)
    assert (j["country"], j["description"]) == ("United States", "2+ years of experience with LLMs.")


def test_smartrecruiters_passes_non_us_through_with_detail_url(monkeypatch):
    fake_get(monkeypatch, {"content": [{
        "id": "744", "name": "Software Engineer", "releasedDate": ISO,
        "location": {"city": "Pune", "country": "in"},
    }]})
    [j] = fetchers.fetch_smartrecruiters("Acme", hours=30)
    assert j["country"] == "in"
    assert j["detail_url"] == "https://api.smartrecruiters.com/v1/companies/Acme/postings/744"


def test_workday_detail_url_and_job_id(monkeypatch):
    def post(url, json=None, **kw):
        return Resp({"jobPostings": [{
            "title": "Software Engineer", "postedOn": "Posted Today",
            "externalPath": "/job/US-CA-Santa-Clara/Software-Engineer_JR1",
            "locationsText": "US, CA, Santa Clara"}]})
    monkeypatch.setattr(fetchers.requests, "post", post)
    [j] = fetchers.fetch_workday("https://acme.wd5.myworkdayjobs.com", "acme/External", hours=30)
    assert j["job_id"] == "/job/US-CA-Santa-Clara/Software-Engineer_JR1"
    assert j["detail_url"] == ("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External"
                               "/job/US-CA-Santa-Clara/Software-Engineer_JR1")
    assert j["job_link"] == ("https://acme.wd5.myworkdayjobs.com/External"
                             "/job/US-CA-Santa-Clara/Software-Engineer_JR1")


def test_no_filter_helpers_left():
    assert not hasattr(fetchers, "matches_role")
    assert not hasattr(fetchers, "matches_location")
