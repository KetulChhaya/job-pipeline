from filters import cap, company_key, full_check, merge, score


def facts(**kw):
    base = {"role": "Software Engineer", "country": "", "description": "text",
            "yoe_min": None, "yoe_max": None, "sponsorship": "unknown",
            "sponsorship_phrase": "", "applied_ai_hit": False,
            "user_groups": [], "ai_groups": []}
    base.update(kw)
    return base


def test_full_check_drops_high_yoe(prof):
    c = full_check(facts(yoe_min=7), prof)
    assert (c.keep, c.reason, c.detail) == (False, "yoe", "7+")


def test_full_check_keeps_five_years(prof):
    assert full_check(facts(role="Senior Software Engineer", yoe_min=5), prof).keep


def test_senior_unknown_yoe_follows_knob(prof):
    j = facts(role="Senior Software Engineer")
    assert full_check(j, prof).keep
    strict = dict(prof, senior_unknown_yoe="drop")
    assert full_check(j, strict).reason == "yoe"


def test_full_check_sponsorship(prof):
    c = full_check(facts(sponsorship="no", sponsorship_phrase="unable to sponsor"), prof)
    assert (c.keep, c.reason, c.detail) == (False, "sponsorship", "unable to sponsor")
    relaxed = dict(prof, needs_sponsorship=False)
    assert full_check(facts(sponsorship="no"), relaxed).keep
    assert full_check(facts(sponsorship="clearance"), relaxed).reason == "sponsorship"


def test_full_check_structured_country_from_detail(prof):
    assert full_check(facts(country="Israel"), prof).reason == "location"
    assert full_check(facts(country="United States of America"), prof).keep
    assert full_check(facts(country="US | EU"), prof).keep
    assert full_check(facts(country="in"), prof).reason == "location"


def test_ml_requires_applied_ai(prof):
    ml = facts(role="Senior Machine Learning Engineer, Personalization")
    assert full_check(ml, prof).detail == "ml-not-applied-ai"
    assert full_check(dict(ml, applied_ai_hit=True), prof).keep
    assert full_check(dict(ml, description=""), prof).keep  # no text: keep, tagged elsewhere


def test_score_weights(prof):
    best = facts(user_groups=["typescript", "node", "react", "python", "python_web"],
                 ai_groups=["llm", "rag", "agents", "frameworks", "prompt"],
                 yoe_min=3, sponsorship="yes")
    assert score(best, prof) == 100
    assert score(facts(), prof) == 8 + 5
    assert score(facts(user_groups=["python"], yoe_min=2), prof) == 10 + 15 + 5


def test_company_key():
    assert company_key("Gusto, Inc.") == "gusto"
    assert company_key("Acme LLC") == company_key("acme")


def test_merge_collapses_same_title_across_locations():
    jobs = [
        {"company": "nvidia", "role": "Senior Software Engineer - Storage",
         "location": "US, CA, Santa Clara", "job_link": "a", "score": 40},
        {"company": "nvidia", "role": "Senior Software Engineer – Storage",
         "location": "US, TX, Austin", "job_link": "b", "score": 55},
        {"company": "nvidia", "role": "Senior Software Engineer - Storage",
         "location": "US, WA, Redmond", "job_link": "c", "score": 10},
        {"company": "nvidia", "role": "Software Engineer", "location": "US, CA",
         "job_link": "d", "score": 30},
    ]
    merged, folded = merge(jobs)
    assert [j["job_link"] for j in merged] == ["b", "d"]
    assert merged[0]["location"] == "US, TX, Austin; US, CA, Santa Clara (+1 more)"
    assert [(j["job_link"], w) for j, w in folded] == [("a", "b"), ("c", "b")]


def test_cap_counts_rows_already_appended_today(prof):
    jobs = [{"company": "nvidia", "job_link": str(i), "score": s}
            for i, s in enumerate([10, 90, 50, 70])]
    jobs.append({"company": "Stripe", "job_link": "s", "score": 5})
    kept, capped = cap(jobs, prof, {"nvidia": 1})
    assert [j["job_link"] for j in kept] == ["1", "3", "s"]
    assert [d for _, d in capped] == ["nvidia: 3 today", "nvidia: 3 today"]
