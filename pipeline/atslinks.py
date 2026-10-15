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
