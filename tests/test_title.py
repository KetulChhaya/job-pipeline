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
