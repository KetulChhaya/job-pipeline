"""Description facts for filters.py: get the text (cache-first), then extract
YOE, stack terms and sponsorship wording. No keep/drop decisions here.
"""
import re
import time
from datetime import datetime, timedelta

import requests

from add_job import normalize_url
from fetchers import TIMEOUT, UA, html_to_text

DETAIL_SLEEP = 1  # seconds between detail requests (SmartRecruiters / Workday)

# ----------------------------------------------------------------------- YOE
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen".split())}
_NUM = (r"(?<![A-Za-z0-9])(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|"
        r"ten|eleven|twelve|thirteen|fourteen|fifteen)")
_Y = r"(?:years?|yrs?)(?![\s-]*(?:degree|college|university|program))"
_YOE_PATTERNS = [  # order matters: earlier matches are blanked out
    (re.compile(rf"{_NUM}\s*\+?\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*{_Y}", re.I), True),
    (re.compile(rf"(?:at least|minimum(?: of)?|min\.?)\s*{_NUM}\s*\+?\s*{_Y}", re.I), False),
    (re.compile(rf"{_NUM}\s*\+\s*{_Y}", re.I), False),
    (re.compile(rf"{_NUM}\s*(?:or more|plus)\s*{_Y}", re.I), False),
    (re.compile(rf"{_NUM}\s*{_Y}", re.I), False),
]
_ANY_YEARS = re.compile(rf"\d\s*\+?\s*{_Y}", re.I)
_PREF_HEAD = re.compile(r"\b(?:preferred|nice[ -]to[ -]haves?|bonus|desired|pluses)\b", re.I)
_REQ_HEAD = re.compile(
    r"\b(?:minimum|basic|required|requirements|qualifications|what you(?:'ll)? (?:need|bring)|"
    r"you have|about you|who you are|what we(?:'re)? looking for|responsibilities)\b", re.I)
_SKIP = re.compile(r"\b(?:preferred|bonus|nice[ -]to[ -]have|a plus|ideally|desired)\b", re.I)
_EXPERIENCE = re.compile(r"experience", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")


def _num(s):
    s = s.lower()
    return int(s) if s.isdigit() else _WORDS.get(s)


def _requirement_sentences(text):
    """Sentences that state a requirement: mention experience, not under a
    'Preferred' heading, and not themselves marked preferred/bonus."""
    mode = "req"
    for raw in (text or "").replace("’", "'").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if len(line) <= 60 and not _ANY_YEARS.search(line):
            if _PREF_HEAD.search(line):
                mode = "pref"
                continue
            if _REQ_HEAD.search(line):
                mode = "req"
                continue
        if mode == "pref":
            continue
        for sent in _SENTENCE.split(line):
            if _EXPERIENCE.search(sent) and not _SKIP.search(sent):
                yield sent


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
