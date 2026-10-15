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
