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
