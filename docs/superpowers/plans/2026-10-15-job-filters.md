# Job Filters + Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Only jobs that fit the candidate profile (2–5 YOE; backend / full-stack / applied-AI; needs sponsorship; US) reach the "Pipeline Jobs" tab, each with YOE, a stack summary and a fit score.

**Architecture:** Fetchers stop filtering and return every fresh posting with `ats`, `job_id`, `country`, `description` and `detail_url`. `discover.py` runs: `quick_check` (title + location) → `enrich_job` (description, cache-first; extracts YOE / stack / sponsorship) → `full_check` → `score` → `merge` → `cap`, then writes `runs/candidates_*.json` and `runs/rejected_*.json`. `add_job.py` stays the only sheet writer; it appends best-score-first and logs drop counts.

**Tech Stack:** Python 3.14 (`.venv`), `requests`, `gspread`, SQLite (`pipeline/seen.db`), `pytest` (dev only).

**Spec:** `docs/superpowers/specs/2026-10-15-job-filters-design.md`

**Prototype note:** every code block below was run in an isolated copy of this repo on 2026-10-15: 196 tests passed, plus a live run against the 54 configured boards. Copy blocks exactly.

## Global Constraints

- Run tests with `.venv/bin/python -m pytest -q` from the repo root.
- Sheet is append-only. The 9 "Pipeline Jobs" columns do not change. Notes must start with `source: ` (existing rows and the registry depend on it).
- `max_yoe: 5`, `per_company_per_day: 3`, `needs_sponsorship: true`, `senior_unknown_yoe: "keep"`, `ambiguous_remote: "keep"`.
- If a check cannot decide (no description, no YOE stated, no sponsorship wording, ambiguous location), the job is **kept and tagged `?`**, never silently dropped.
- Agents must not edit `config/profile.json`, `pipeline/filters.py`, `pipeline/enrich.py`.
- No new runtime dependencies. `pytest` goes in `requirements-dev.txt` only.
- Never stage `service-account.json`. Do not run `run_claude.sh` / `run_gemini.sh` until Task 12.

## Deliberate refinements of the spec (decided while prototyping)

1. `html_to_text` lives in `fetchers.py`: fetchers need it while parsing, and it avoids an `enrich` ↔ `fetchers` import cycle.
2. `discover.finalize()` writes `yoe` / `stack` / `notes`; `add_job.py` keeps writing whatever the candidate carries. Old candidate files keep working.
3. Jobs already in the `seen` registry are skipped before filtering: no wasted detail requests, and they don't eat a per-company cap slot twice.
4. Rows folded by `merge()` are stored in `rejected` with reason `merged` and skipped on later runs; otherwise they'd come back alone next run.
5. `profile.location` uses `us_cities` / `non_us_strong` / `non_us_weak`. Strong terms (countries, unambiguous foreign cities) beat a US signal; weak ones (`london`, `dublin`, `paris`, `cambridge`, `vancouver`) only count when there is no US signal. Example: "Dublin, CA" is US, "Vancouver, Washington" is US.
6. Small additions to the drop list: SDET, QA / test / quality engineer, software engineer in test, penetration testing; "federal" added to the citizenship title markers next to GovCloud. Added to the keep list: "software platform engineer". The last two came from the live prototype run: "Penetration Testing Engineer, MTS" and "Software Development Engineer (US Federal)" were wrongly kept.

## Review Focus

Most likely to bite a real run; each has a test in its owning task:

1. Curly apostrophes in sponsorship text ("We can’t sponsor visas") must count as "no sponsorship". Task 5, `test_extract_sponsorship`.
2. A candidates file from before this change (no `score`, no `source`) must still append. Task 10, `test_old_format_candidates_still_work`.
3. Greenhouse sends entity-escaped HTML (`&lt;li&gt;`, `&amp;nbsp;`); text and YOE must come out clean. Task 4, `test_html_to_text_handles_escaped_greenhouse_content`.
4. A Workday / SmartRecruiters detail request that 404s or times out must keep the job (`desc ?`) and must not write an empty cache row. Task 5, `test_enrich_fetch_failure_keeps_job_tagged`.
5. "8+ years" under a "Preferred qualifications" heading must not drop a job whose minimum is 4. Task 4, `test_preferred_section_is_ignored` and `test_required_section_after_preferred_counts_again`.

## File map

| File | Status | Responsibility |
|---|---|---|
| `.gitignore`, `requirements-dev.txt` | new | repo hygiene, pytest |
| `config/profile.json` | new | every filter setting |
| `pipeline/filters.py` | new | `load_profile`, `classify_location`, `quick_check`, `is_senior`, `full_check`, `score`, `merge`, `cap`, `company_key` |
| `pipeline/enrich.py` | new | `extract_yoe`, `extract_sponsorship`, `extract_stack`, `matched_groups`, `fetch_detail`, `enrich_job`, `init_cache`, `prune_cache` |
| `pipeline/fetchers.py` | modified | `html_to_text`; no role/location filtering; new job fields |
| `pipeline/discover.py` | rewritten | orchestration, `run_filters`, `finalize`, `init_rejected`, `count_today`, writes `runs/` |
| `pipeline/add_job.py` | modified | score order, `Dropped` column, `dropped_summary` |
| `config/boards.json` | modified | `title_keywords` / `location_keywords` removed |
| `prompts/claude.md`, `prompts/gemini.md`, `README.md` | modified | new paths and rules |
| `tests/…` | new | 196 tests |

---

### Task 0: Repo baseline

The folder is not a git repo yet. This task makes it one without ever staging the key.

**Files:**
- Create: `.gitignore`
- Create: `requirements-dev.txt`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
.venv/
__pycache__/
.DS_Store
service-account.json
pipeline/seen.db
logs/
runs/
candidates_*.json
```

- [ ] **Step 2: Init and prove the key is ignored**

Run: `git init && git check-ignore -v service-account.json pipeline/seen.db`
Expected: two lines, both pointing at `.gitignore`.

- [ ] **Step 3: Add pytest as a dev dependency**

Create `requirements-dev.txt`:

```text
-r requirements.txt
pytest>=8
```

Run: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest --version`
Expected: `pytest 8.x` or newer.

- [ ] **Step 4: Baseline commit**

Run: `git add -A && git status --short`
Expected: no line contains `service-account.json`, `seen.db`, `logs/` or `candidates_`.

```bash
git commit -m "chore: baseline before job filters"
```

---

### Task 1: Candidate profile and loader

**Files:**
- Create: `config/profile.json`
- Create: `pipeline/filters.py` (header + `load_profile`)
- Create: `tests/conftest.py`
- Test: `tests/test_profile.py`

**Interfaces:**
- Produces: `load_profile(path: str) -> dict`. Raises `ValueError` (message contains `cannot read profile`, `missing keys: …` or `bad pattern`). The returned dict is the JSON plus `prof["_re"]`, which holds compiled patterns: `us_states`, `us_aliases`, `us_cities`, `non_us_strong`, `non_us_weak`, `family_keep`, `family_drop`, `frontend`, `ml_titles`, `ml_requires_any`, `level_drop`, `level_senior`, `citizenship_title`, `sponsor_neg`, `sponsor_pos`, `clearance` (each a `re.Pattern`), `user_groups` / `ai_groups` (`dict[str, re.Pattern]`), `dictionary` (`list[tuple[str, re.Pattern]]`).
- Produces: `Check = namedtuple("Check", "keep reason detail tags")`, `_rx(fragments)`, `_collapse(s)`, `US_STATES`, `US_STATE_CODES`, `US_ALIASES`.
- Produces (fixture): `prof`, a session-scoped pytest fixture that loads `config/profile.json`.

- [ ] **Step 1: Write the test scaffolding and failing tests**

`tests/conftest.py`:

```python
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from filters import load_profile  # noqa: E402


@pytest.fixture(scope="session")
def prof():
    return load_profile(os.path.join(ROOT, "config", "profile.json"))
```

`tests/test_profile.py`:

```python
import json

import pytest

from filters import load_profile


def test_real_profile_loads(prof):
    assert prof["max_yoe"] == 5
    assert prof["per_company_per_day"] == 3
    assert prof["needs_sponsorship"] is True
    assert "family_keep" in prof["_re"]


def test_missing_key_is_reported(tmp_path, prof):
    raw = {k: v for k, v in prof.items() if k not in ("_re", "weights")}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="missing keys: weights"):
        load_profile(str(p))


def test_bad_regex_is_reported(tmp_path, prof):
    raw = {k: v for k, v in prof.items() if k != "_re"}
    raw["level"] = {"drop": ["staff("], "senior": ["senior"]}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="bad pattern"):
        load_profile(str(p))


def test_invalid_json_is_reported(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="cannot read profile"):
        load_profile(str(p))
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_profile.py`
Expected: collection error, `ModuleNotFoundError: No module named 'filters'`.

- [ ] **Step 3: Write `config/profile.json`**

```json
{
 "_comment": "Candidate filter profile. Agents must NOT edit this file. Spec: docs/superpowers/specs/2026-10-15-job-filters-design.md. Patterns are case-insensitive regex fragments matched on whitespace-collapsed text with letter/digit boundaries; wrap a part in (?-i:...) to make it case-sensitive.",
 "max_yoe": 5,
 "senior_unknown_yoe": "keep",
 "per_company_per_day": 3,
 "needs_sponsorship": true,
 "location": {
  "ambiguous_remote": "keep",
  "us_cities": [
   "san francisco", "sf", "bay area", "seattle", "new york city", "nyc", "brooklyn",
   "boston", "austin", "los angeles", "chicago", "denver", "boulder", "atlanta",
   "sunnyvale", "mountain view", "palo alto", "menlo park", "san jose", "santa clara",
   "cupertino", "san mateo", "redwood city", "pleasanton", "irvine", "san diego",
   "redmond", "bellevue", "kirkland", "pittsburgh", "philadelphia", "miami", "dallas",
   "houston", "portland", "salt lake city", "raleigh", "durham", "nashville", "phoenix",
   "minneapolis", "detroit", "columbus", "charlotte", "st\\.? louis", "kansas city",
   "jersey city", "hoboken"
  ],
  "non_us_strong": [
   "canada", "toronto", "montreal", "ottawa", "waterloo", "calgary",
   "ontario", "quebec", "british columbia", "brazil", "brasil", "s[ãa]o paulo", "mexico",
   "india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "gurgaon",
   "gurugram", "noida", "mumbai", "united kingdom", "uk", "england", "scotland",
   "ireland", "germany", "berlin", "munich", "france", "spain", "madrid", "barcelona",
   "netherlands", "amsterdam", "poland", "warsaw", "krakow", "singapore", "japan",
   "tokyo", "australia", "sydney", "melbourne", "israel", "tel aviv", "portugal",
   "lisbon", "romania", "bucharest", "serbia", "belgrade", "argentina", "buenos aires",
   "colombia", "bogota", "chile", "philippines", "manila", "vietnam", "china", "beijing",
   "shanghai", "shenzhen", "hong kong", "korea", "seoul", "taiwan", "taipei", "czech",
   "prague", "sweden", "stockholm", "switzerland", "zurich", "italy", "milan", "belgium",
   "brussels", "denmark", "copenhagen", "finland", "helsinki", "norway", "oslo",
   "austria", "vienna", "hungary", "budapest", "ukraine", "kyiv", "turkey", "istanbul",
   "egypt", "cairo", "nigeria", "lagos", "kenya", "nairobi", "south africa", "uae",
   "dubai", "abu dhabi", "saudi", "riyadh", "pakistan", "new zealand", "greece",
   "athens", "estonia", "lithuania", "latvia", "bulgaria", "croatia", "costa rica",
   "uruguay", "peru", "latam", "emea", "apac", "europe", "asia"
  ],
  "non_us_weak": ["london", "dublin", "paris", "cambridge", "vancouver"]
 },
 "family": {
  "keep": [
   "software engineer(?:ing)?", "software developer", "software development engineer",
   "sde", "mts", "member of (?:the )?technical staff", "back-?end", "back end",
   "full-?stack", "full stack", "ai engineer", "applied ai", "llm engineer",
   "gen ?ai engineer", "generative ai engineer", "agents? engineer", "product engineer",
   "api engineer", "software platform engineer", "machine learning engineer", "ml engineer"
  ],
  "drop": [
   "system software", "systems software", "soc", "firmware", "embedded", "kernel",
   "drivers?", "cuda", "asic", "fpga", "silicon", "eda", "validation engineer",
   "verification engineer", "design verification", "system validation", "hardware",
   "mfg", "manufacturing", "security engineer", "cybersecurity", "penetration", "pen ?test(?:ing|er)?", "sales engineer",
   "solutions? engineer", "support engineer", "solutions architect", "devops", "sre",
   "site reliability", "data engineer", "mobile", "ios", "android", "client platform",
   "network engineer", "(?<!software )platform engineer", "sdet", "qa engineer",
   "test engineer", "software engineer in test", "quality engineer"
  ],
  "frontend": ["front-?end", "front end"],
  "ml_titles": ["machine learning engineer", "ml engineer"],
  "ml_requires_any": [
   "llms?", "large language models?", "rag", "retrieval[- ]augmented", "agents?",
   "agentic", "gen ?ai", "generative ai", "bedrock", "langchain", "prompt(?:s|ing)?"
  ]
 },
 "level": {
  "drop": [
   "staff", "principal", "lead", "architect", "distinguished", "fellow", "manager",
   "director", "head of", "vp", "vice president", "intern", "internship",
   "new grad(?:uate)?", "new college grad(?:uate)?", "college grad(?:uate)?",
   "graduate", "university", "amts", "lmts", "pmts"
  ],
  "senior": ["senior", "sr\\.?", "snr", "smts"]
 },
 "citizenship_title": ["govcloud", "federal", "cleared", "clearance", "ts/sci", "top secret", "secret"],
 "sponsorship": {
  "negative": [
   "(?:unable|not able|cannot|can ?not|can't|will not|won't|do not|does not|don't|doesn't|are not able|is not able) (?:to )?(?:provide |offer |support )?(?:visa |immigration |employment )?sponsor(?:ship|ing)?",
   "without (?:the need for |requiring )?(?:current or future |now or in the future |future )?(?:visa |employment |immigration |work )?sponsorship",
   "no (?:visa |immigration )?sponsorship",
   "sponsorship (?:is )?not (?:available|offered|provided|supported)",
   "not (?:eligible|available) for (?:visa |immigration )?sponsorship",
   "must be a u\\.?s\\.? citizen",
   "u\\.?s\\.? citizen(?:ship)? (?:is )?(?:required|only)",
   "u\\.?s\\.? citizens only",
   "green card holders?"
  ],
  "clearance": [
   "(?:active|current|ability to obtain|able to obtain|eligib(?:le|ility) (?:for|to obtain)|must (?:obtain|hold|possess|have)) (?:an? |and maintain (?:an? )?)?(?:[a-z/]+ ){0,3}?clearance",
   "ts/sci", "top secret", "secret clearance", "itar", "u\\.?s\\.? persons?"
  ],
  "positive": [
   "(?:will|can|able to|happy to|glad to) sponsor",
   "(?:visa )?sponsorship (?:is )?available",
   "h-?1b (?:visa )?(?:sponsorship|transfers?) (?:is |are )?(?:available|supported|welcome)",
   "we sponsor (?:visas|h-?1b)"
  ]
 },
 "stack": {
  "user_groups": {
   "typescript": "typescript|javascript",
   "node": "node\\.?js|(?-i:Node)|nest\\.?js|express\\.?js",
   "react": "react\\.?js|(?-i:React)|next\\.?js",
   "python": "python",
   "python_web": "fastapi|django|(?-i:Flask)"
  },
  "ai_groups": {
   "llm": "llms?|large language models?|gen ?ai|generative ai",
   "rag": "rag|retrieval[- ]augmented|embeddings?|vector (?:db|database|store|search)|pgvector|pinecone|weaviate",
   "agents": "ai agents?|agentic|multi-agent|llm agents?|agent (?:framework|development|platform|orchestration)",
   "frameworks": "(?-i:Bedrock)|langchain|llamaindex|openai|anthropic|(?-i:Claude)",
   "prompt": "prompt engineering|prompt design|prompting|system prompts?"
  },
  "user_terms": ["Python", "TypeScript", "JavaScript", "Node.js", "React", "Next.js", "FastAPI", "Django", "Flask"],
  "dictionary": {
   "Python": "python", "TypeScript": "typescript", "JavaScript": "javascript",
   "Node.js": "node\\.?js|(?-i:Node)", "React": "react\\.?js|(?-i:React)",
   "Next.js": "next\\.?js", "FastAPI": "fastapi", "Django": "django", "Flask": "(?-i:Flask)",
   "Java": "(?-i:Java)", "Kotlin": "kotlin", "Go": "golang|(?-i:Go)", "Rust": "(?-i:Rust)",
   "C++": "c\\+\\+", "C#": "c#", "Ruby": "ruby", "Scala": "(?-i:Scala)",
   "GraphQL": "graphql", "Express": "express\\.?js", "NestJS": "nest\\.?js",
   "Spring": "spring boot|(?-i:Spring)", "Rails": "ruby on rails|(?-i:Rails)", ".NET": "\\.net",
   "AWS": "aws|amazon web services", "GCP": "gcp|google cloud", "Azure": "(?-i:Azure)",
   "Kubernetes": "kubernetes|k8s", "Docker": "docker", "Terraform": "terraform",
   "Lambda": "(?-i:Lambda)", "Postgres": "postgres(?:ql)?", "MySQL": "mysql",
   "MongoDB": "mongo(?:db)?", "Redis": "redis", "Kafka": "kafka", "Spark": "(?-i:Spark)",
   "Snowflake": "(?-i:Snowflake)", "DynamoDB": "dynamo(?:db)?",
   "LLM": "llms?|large language models?|gen ?ai|generative ai", "RAG": "rag|retrieval[- ]augmented",
   "Agents": "ai agents?|agentic|multi-agent|llm agents?", "Bedrock": "(?-i:Bedrock)",
   "LangChain": "langchain", "OpenAI": "openai", "Claude": "anthropic|(?-i:Claude)",
   "Embeddings": "embeddings?",
   "Vector DB": "vector (?:db|database|store|search)|pgvector|pinecone|weaviate|qdrant",
   "PyTorch": "pytorch"
  }
 },
 "weights": {
  "user_stack_per_group": 10,
  "ai_per_group": 5,
  "yoe_known": 15,
  "yoe_unknown": 8,
  "sponsor_yes": 10,
  "sponsor_unknown": 5
 }
}
```

- [ ] **Step 4: Write `pipeline/filters.py` (header and loader only)**

```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_profile.py`
Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add config/profile.json pipeline/filters.py tests/conftest.py tests/test_profile.py
git commit -m "feat(filters): candidate profile and loader"
```

---

### Task 2: Location classification

**Files:**
- Modify: `pipeline/filters.py` (append location section)
- Test: `tests/test_location.py`

**Interfaces:**
- Consumes: `prof["_re"]` from Task 1.
- Produces: `classify_location(location: str, country: str, prof: dict) -> "us" | "non_us" | "ambiguous"`. A non-empty `country` always wins over the text.

- [ ] **Step 1: Write the failing test**

`tests/test_location.py`:

```python
import pytest

from filters import classify_location


@pytest.mark.parametrize("location,country,expected", [
    ("Bengaluru, Karnataka, IN", "", "non_us"),
    ("Remote, Israel", "", "non_us"),
    ("London or Remote", "", "non_us"),
    ("Remote - Portugal", "", "non_us"),
    ("Romania Remote (2 Locations)", "", "non_us"),
    ("Toronto, ON, Canada", "", "non_us"),
    ("Toronto, CA", "", "non_us"),
    ("Berlin, DE", "", "non_us"),
    ("Remote, Global", "", "ambiguous"),
    ("Remote", "", "ambiguous"),
    ("", "", "ambiguous"),
    ("USAVAReston (2 Locations)", "", "ambiguous"),
    ("Seattle, WA", "", "us"),
    ("Remote (US)", "", "us"),
    ("San Francisco, Remote", "", "us"),
    ("New York", "", "us"),
    ("Albuquerque, New Mexico", "", "us"),
    ("Dublin, CA", "", "us"),
    ("Vancouver, Washington, United States of America", "", "us"),
    ("Vancouver, BC", "", "non_us"),
    ("Portland, OR", "", "us"),
    ("Menlo Park, CA; New York, NY", "", "us"),
    ("San Francisco, CA; London, UK", "", "us"),
    ("US, CA, Santa Clara, US CA Santa Clara", "", "us"),
    ("Remote (Canada or US)", "", "us"),
    ("Texas, USA - Remote, Texas USA   Remote", "", "us"),
    ("O'Fallon, Missouri, OFallon Missouri", "", "us"),
    # structured country always wins over the text
    ("Remote", "Israel", "non_us"),
    ("Tel Aviv", "us", "us"),
    ("Anywhere", "United States of America", "us"),
    ("Santa Clara", "in", "non_us"),
])
def test_classify_location(prof, location, country, expected):
    assert classify_location(location, country, prof) == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_location.py`
Expected: `ImportError: cannot import name 'classify_location'`.

- [ ] **Step 3: Append to the end of `pipeline/filters.py`**

```python
# ------------------------------------------------------------------ location
def _segment_kind(seg, R):
    without_states = R["us_states"].sub(" ", seg)  # "New Mexico" is not Mexico
    if R["non_us_strong"].search(without_states):
        return "non_us"
    if (US_STATE_CODES.search(seg) or R["us_states"].search(seg)
            or R["us_aliases"].search(seg) or R["us_cities"].search(seg)):
        return "us"
    if R["non_us_weak"].search(seg):
        return "non_us"
    return "unknown"


def classify_location(location, country, prof):
    """'us' | 'non_us' | 'ambiguous'. A structured country always wins."""
    c = (country or "").strip().lower()
    if c:
        return "us" if c in US_ALIASES else "non_us"
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_location.py`
Expected: `31 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/filters.py tests/test_location.py
git commit -m "feat(filters): US location classification"
```

---

### Task 3: Title checks (`quick_check`)

**Files:**
- Modify: `pipeline/filters.py` (append title section)
- Test: `tests/test_title.py`

**Interfaces:**
- Consumes: `classify_location`, `Check`, `_collapse`.
- Produces: `quick_check(job: dict, prof: dict) -> Check`, reading `job["role"]`, `job["location"]`, `job["country"]`. Reasons: `location`, `family`, `level`, `sponsorship`. On keep, `tags` is `("loc ?",)` for an ambiguous location, else `()`.
- Produces: `is_senior(title: str, prof: dict) -> bool`.

- [ ] **Step 1: Write the failing test**

`tests/test_title.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_title.py`
Expected: `ImportError: cannot import name 'is_senior'`.

- [ ] **Step 3: Append to the end of `pipeline/filters.py`**

```python
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
    m = R["level_drop"].search(_level_title(tl))
    if m:
        return Check(False, "level", m.group(0), ())
    if prof["needs_sponsorship"]:
        m = R["citizenship_title"].search(tl)
        if m:
            return Check(False, "sponsorship", m.group(0), ())
    return Check(True, None, None, tags)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_title.py`
Expected: `49 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/filters.py tests/test_title.py
git commit -m "feat(filters): role family, level and citizenship title checks"
```

---

### Task 4: HTML to text and YOE extraction

**Files:**
- Modify: `pipeline/fetchers.py` (add `import html` and `html_to_text`)
- Create: `pipeline/enrich.py` (header + YOE section)
- Test: `tests/test_yoe.py`

**Interfaces:**
- Produces: `fetchers.html_to_text(s: str) -> str`. One line per block element; entities unescaped twice, because Greenhouse escapes its HTML.
- Produces: `enrich.extract_yoe(text: str) -> tuple[int | None, int | None]`, giving `(min, max)`. `max` is `None` for "N+". With several numbers, the largest minimum wins.

- [ ] **Step 1: Write the failing test**

`tests/test_yoe.py`:

```python
import pytest

from enrich import extract_yoe
from fetchers import html_to_text


@pytest.mark.parametrize("text,expected", [
    ("3+ years of experience building web services.", (3, None)),
    ("3-5 years of experience with Python.", (3, 5)),
    ("3–5 years of professional experience.", (3, 5)),
    ("2 to 4 years of experience in backend development.", (2, 4)),
    ("Three or more years of experience shipping software.", (3, None)),
    ("A minimum of 4 years of experience in software engineering.", (4, None)),
    ("At least 5 years of industry experience.", (5, None)),
    ("(6+ years) of professional experience", (6, None)),
    ("5+ years of experience overall, 2+ years with React.", (5, None)),
    ("Bachelor's degree or 4 year degree and 2+ years of experience.", (2, None)),
    ("10+ years of experience leading teams.", (10, None)),
    ("We were founded 10 years ago and serve 5 million users.", (None, None)),
    ("Strong experience with distributed systems.", (None, None)),
    ("", (None, None)),
])
def test_extract_yoe(text, expected):
    assert extract_yoe(text) == expected


def test_preferred_section_is_ignored():
    text = ("Minimum qualifications:\n4+ years of experience with Python\n"
            "Preferred qualifications:\n8+ years of experience\n")
    assert extract_yoe(text) == (4, None)


def test_required_section_after_preferred_counts_again():
    text = ("Preferred qualifications:\n8+ years of experience\n"
            "Minimum qualifications:\n4+ years of experience with Python\n")
    assert extract_yoe(text) == (4, None)


def test_inline_nice_to_have_is_ignored():
    assert extract_yoe("Nice to have: 8+ years of experience with Go.") == (None, None)


def test_html_to_text_handles_escaped_greenhouse_content():
    raw = ("&lt;h3&gt;Requirements&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;5+ years of experience"
           "&amp;nbsp;with Python&lt;/li&gt;&lt;li&gt;AT&amp;amp;T scale&lt;/li&gt;&lt;/ul&gt;")
    text = html_to_text(raw)
    assert text.splitlines() == ["Requirements", "5+ years of experience with Python",
                                 "AT&T scale"]
    assert extract_yoe(text) == (5, None)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_yoe.py`
Expected: collection error, `No module named 'enrich'`.

- [ ] **Step 3: Add `html_to_text` to `pipeline/fetchers.py`**

Add `import html` above `import json`. Then add this function directly above `def matches_role` (Task 8 rewrites the file and keeps it):

```python
def html_to_text(s):
    """HTML (possibly entity-escaped, as Greenhouse sends it) to plain text,
    one line per block element."""
    s = html.unescape(s or "")
    s = re.sub(r"(?i)<\s*/?\s*(?:br|p|li|ul|ol|div|h[1-6]|tr)\b[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v\xa0]+", " ", s)
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())
```

- [ ] **Step 4: Create `pipeline/enrich.py` (header and YOE section)**

```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_yoe.py`
Expected: `18 passed`.

- [ ] **Step 6: Commit**

```bash
git add pipeline/fetchers.py pipeline/enrich.py tests/test_yoe.py
git commit -m "feat(enrich): html_to_text and YOE extraction"
```

---

### Task 5: Sponsorship, stack, and description fetch

**Files:**
- Modify: `pipeline/enrich.py` (append two sections)
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: `extract_yoe`, `html_to_text`, `normalize_url` (from `add_job`), `prof["_re"]`.
- Produces: `extract_sponsorship(text, prof) -> (status, phrase)`, where status is one of `clearance`, `no`, `yes`, `unknown`.
- Produces: `matched_groups(text, groups) -> list[str]` and `extract_stack(text, prof, limit=6) -> list[str]` (user stack first).
- Produces: `init_cache(con)`, `prune_cache(con, days=3)` and `fetch_detail(job) -> (description, country)`. `fetch_detail` raises on HTTP errors.
- Produces: `enrich_job(job, prof, con=None, fetch=fetch_detail) -> job`. It sets `description`, `country` (if learned), `yoe_min`, `yoe_max`, `sponsorship`, `sponsorship_phrase`, `user_groups`, `ai_groups`, `stack_terms` and `applied_ai_hit`. It appends `"desc ?"` to `job["tags"]` when there is no text. The cache table is `desc_cache(url_key, description, country, fetched_at)`.

- [ ] **Step 1: Write the failing test**

`tests/test_enrich.py`:

```python
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


def test_prune_cache_drops_old_rows(con):
    con.execute("INSERT INTO desc_cache VALUES ('old','x','','2026-01-01T00:00:00')")
    con.execute("INSERT INTO desc_cache VALUES ('new','x','','2999-01-01T00:00:00')")
    prune_cache(con, days=3)
    assert [r[0] for r in con.execute("SELECT url_key FROM desc_cache")] == ["new"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_enrich.py`
Expected: `ImportError: cannot import name 'enrich_job'`.

- [ ] **Step 3: Append to the end of `pipeline/enrich.py`**

```python
# ---------------------------------------------------------- sponsorship/stack
def extract_sponsorship(text, prof):
    """('clearance' | 'no' | 'yes' | 'unknown', matched phrase)."""
    t = " ".join((text or "").replace("’", "'").split())
    R = prof["_re"]
    for status, rx in (("clearance", R["clearance"]), ("no", R["sponsor_neg"]),
                       ("yes", R["sponsor_pos"])):
        m = rx.search(t)
        if m:
            return status, m.group(0)
    return "unknown", ""


def matched_groups(text, groups):
    """Names of the compiled groups that occur in text, in profile order."""
    t = " ".join((text or "").split())
    return [name for name, rx in groups.items() if rx.search(t)]


def extract_stack(text, prof, limit=6):
    """Dictionary terms found in text, the user's own stack first."""
    t = " ".join((text or "").split())
    found = [name for name, rx in prof["_re"]["dictionary"] if rx.search(t)]
    user = set(prof["stack"]["user_terms"])
    return ([n for n in found if n in user] + [n for n in found if n not in user])[:limit]


# ------------------------------------------------------------- descriptions
def init_cache(con):
    con.execute("""CREATE TABLE IF NOT EXISTS desc_cache(
        url_key TEXT PRIMARY KEY, description TEXT, country TEXT, fetched_at TEXT)""")


def prune_cache(con, days=3):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    con.execute("DELETE FROM desc_cache WHERE fetched_at < ?", (cutoff,))


def fetch_detail(job):
    """(description, country) from the job's detail_url. Raises on HTTP errors."""
    r = requests.get(job["detail_url"], headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    time.sleep(DETAIL_SLEEP)
    if job.get("ats") == "smartrecruiters":
        sections = (d.get("jobAd") or {}).get("sections") or {}
        parts = []
        for key in ("jobDescription", "qualifications", "additionalInformation"):
            s = sections.get(key) or {}
            parts.append((s.get("title") or "") + "\n" + html_to_text(s.get("text") or ""))
        return "\n".join(parts).strip(), ""
    if job.get("ats") == "workday":
        info = d.get("jobPostingInfo") or {}
        return (html_to_text(info.get("jobDescription") or ""),
                (info.get("country") or {}).get("descriptor") or "")
    return "", ""


def enrich_job(job, prof, con=None, fetch=fetch_detail):
    """Fill description-derived fields in place and return the job.
    Sets: description, country (if learned), yoe_min, yoe_max, sponsorship,
    sponsorship_phrase, user_groups, ai_groups, stack_terms, applied_ai_hit.
    Appends 'desc ?' to job['tags'] when no description could be had."""
    text = job.get("description") or ""
    if not text and job.get("detail_url"):
        key = normalize_url(job["job_link"])
        row = None
        if con is not None:
            row = con.execute("SELECT description, country FROM desc_cache WHERE url_key=?",
                              (key,)).fetchone()
        if row:
            text, country = row
        else:
            try:
                text, country = fetch(job)
            except Exception:
                text, country = "", ""
            if text and con is not None:
                con.execute("INSERT OR REPLACE INTO desc_cache VALUES (?,?,?,?)",
                            (key, text, country, datetime.now().isoformat(timespec="seconds")))
        if country and not job.get("country"):
            job["country"] = country
    job["description"] = text
    if not text:
        job.setdefault("tags", []).append("desc ?")
    both = f"{job.get('role', '')}\n{text}"
    R = prof["_re"]
    job["yoe_min"], job["yoe_max"] = extract_yoe(text)
    job["sponsorship"], job["sponsorship_phrase"] = extract_sponsorship(text, prof)
    job["user_groups"] = matched_groups(both, R["user_groups"])
    job["ai_groups"] = matched_groups(both, R["ai_groups"])
    job["stack_terms"] = extract_stack(both, prof)
    job["applied_ai_hit"] = bool(R["ml_requires_any"].search(" ".join(text.split())))
    return job
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_enrich.py tests/test_yoe.py`
Expected: `48 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/enrich.py tests/test_enrich.py
git commit -m "feat(enrich): sponsorship, stack terms, cached description fetch"
```

---

### Task 6: `full_check`, score, merge, cap

**Files:**
- Modify: `pipeline/filters.py` (append the last two sections)
- Test: `tests/test_rank.py`

**Interfaces:**
- Consumes: fields set by `enrich_job`.
- Produces: `full_check(job, prof) -> Check`, with reasons `location`, `yoe`, `sponsorship` and `family` (detail `ml-not-applied-ai`).
- Produces: `score(job, prof) -> int` (0–100), `company_key(name) -> str` and `title_key(title) -> str`.
- Produces: `merge(jobs) -> (merged, folded)`, where `folded` is a list of `(job, winner_link)`.
- Produces: `cap(jobs, prof, already_today: dict[str, int]) -> (kept, capped)`, where `capped` is a list of `(job, detail)` and detail looks like `"nvidia: 3 today"`.

- [ ] **Step 1: Write the failing test**

`tests/test_rank.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_rank.py`
Expected: `ImportError: cannot import name 'cap'`.

- [ ] **Step 3: Append to the end of `pipeline/filters.py`**

```python
# --------------------------------------------------------------- description
def full_check(job, prof):
    """Uses fields set by enrich.enrich_job: country, description, yoe_min,
    sponsorship, sponsorship_phrase, applied_ai_hit."""
    country = (job.get("country") or "").strip()
    if country and country.lower() not in US_ALIASES:
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `142 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/filters.py tests/test_rank.py
git commit -m "feat(filters): full_check, score, merge and per-company cap"
```

---

### Task 7: Labeled fixture suite

This is the acceptance test for the rules: 34 jobs with titles and locations taken verbatim from the 2026-10-15 runs. Descriptions are short synthetic excerpts that carry the deciding phrase; a company of "Example Co" marks a fully synthetic case. If a case fails, fix the rule in `filters.py` or `profile.json`. Change a label only if it contradicts the spec.

**Files:**
- Create: `tests/fixtures/jobs.json`
- Test: `tests/test_fixtures.py`

**Interfaces:**
- Consumes: `quick_check`, `enrich_job`, `full_check`, `score`, run in the same order as `discover.run_filters`.

- [ ] **Step 1: Write the fixture file**

`tests/fixtures/jobs.json`:

```json
[
 {"id": "bengaluru-in", "company": "nvidia", "role": "Senior Software Engineer", "location": "Bengaluru, Karnataka, IN", "country": "", "description": "", "expect": {"keep": false, "reason": "location"}},
 {"id": "remote-israel", "company": "Example Co", "role": "Software Engineer", "location": "Remote, Israel", "country": "", "description": "", "expect": {"keep": false, "reason": "location"}},
 {"id": "london-or-remote", "company": "Example Co", "role": "Backend Engineer", "location": "London or Remote", "country": "", "description": "", "expect": {"keep": false, "reason": "location"}},
 {"id": "remote-global", "company": "Example Co", "role": "Software Engineer", "location": "Remote, Global", "country": "", "description": "2+ years of experience with TypeScript.", "expect": {"keep": true, "tags": ["loc ?"]}},
 {"id": "romania-remote", "company": "nvidia", "role": "Senior Software Developer, AI Networking", "location": "Romania Remote (2 Locations)", "country": "", "description": "", "expect": {"keep": false, "reason": "location"}},
 {"id": "structured-israel", "company": "Example Co", "role": "Software Engineer", "location": "Remote", "country": "Israel", "description": "", "expect": {"keep": false, "reason": "location"}},
 {"id": "finops-data", "company": "supabase", "role": "FinOps Data Engineer", "location": "Remote, Global", "country": "", "description": "", "expect": {"keep": false, "reason": "family"}},
 {"id": "eng-manager", "company": "Affirm", "role": "Manager, Software Engineering (App Infrastructure)", "location": "Remote US", "country": "", "description": "", "expect": {"keep": false, "reason": "level"}},
 {"id": "amts", "company": "salesforce", "role": "Software Engineering AMTS (College Grad)", "location": "California   San Francisco (7 Locations)", "country": "", "description": "", "expect": {"keep": false, "reason": "level"}},
 {"id": "staff-plus", "company": "Anthropic", "role": "Staff+ Software Engineer, Platform Acceleration", "location": "San Francisco, CA | New York City, NY", "country": "", "description": "", "expect": {"keep": false, "reason": "level"}},
 {"id": "principal", "company": "Elastic", "role": "Principal Software Engineer I - Platform Billing Experience", "location": "United States", "country": "", "description": "", "expect": {"keep": false, "reason": "level"}},
 {"id": "senior-5-plus", "company": "autodesk", "role": "Senior Software Engineer, Agentic AI", "location": "Texas, USA - Remote, Texas USA   Remote", "country": "", "description": "5+ years of experience building backend services in Python or TypeScript. Experience with LLMs, RAG and agentic workflows.", "expect": {"keep": true, "min_score": 50}},
 {"id": "senior-7-plus", "company": "mastercard", "role": "Senior Software Engineer - Java Full Stack & AI", "location": "O'Fallon, Missouri, OFallon Missouri", "country": "", "description": "7+ years of experience in full stack development with Java and React.", "expect": {"keep": false, "reason": "yoe"}},
 {"id": "unable-to-sponsor", "company": "Example Co", "role": "Software Engineer II", "location": "Austin, TX", "country": "", "description": "3+ years of experience. Example Co is unable to sponsor visas for this role.", "expect": {"keep": false, "reason": "sponsorship"}},
 {"id": "govcloud", "company": "salesforce", "role": "GovCloud Software Engineer", "location": "California   San Francisco (3 Locations)", "country": "", "description": "", "expect": {"keep": false, "reason": "sponsorship"}},
 {"id": "identity-verification", "company": "Example Co", "role": "Software Engineer, Identity Verification", "location": "New York, NY", "country": "", "description": "2+ years of experience with Node.js.", "expect": {"keep": true}},
 {"id": "salesforce-mts", "company": "salesforce", "role": "Software Engineering MTS", "location": "California - San Francisco, California", "country": "", "description": "4+ years of experience in Java or Python.", "expect": {"keep": true}},
 {"id": "ml-personalization", "company": "spotify", "role": "Senior Machine Learning Engineer, Personalization", "location": "New York, NY", "country": "", "description": "5+ years of experience building recommendation systems with TensorFlow and Spark.", "expect": {"keep": false, "reason": "family"}},
 {"id": "ml-llm", "company": "Example Co", "role": "Machine Learning Engineer, Applied LLMs", "location": "San Francisco, CA", "country": "", "description": "3+ years of experience. Build RAG pipelines and LLM evaluation in Python.", "expect": {"keep": true}},
 {"id": "nvidia-systems", "company": "nvidia", "role": "Senior System Software Engineer, ML and Vector Search", "location": "US CA Santa Clara (2 Locations)", "country": "", "description": "", "expect": {"keep": false, "reason": "family"}},
 {"id": "workday-api-platform", "company": "workday", "role": "Sr Software Engineer – API Platform, Product Engineering (Python/Typescript)", "location": "USA, CA, Pleasanton, USA CA Pleasanton", "country": "", "description": "5+ years of experience with Python, TypeScript, Node.js and React. Experience integrating LLMs into products.", "expect": {"keep": true, "min_score": 60}},
 {"id": "client-platform", "company": "openai", "role": "Client Platform Engineer", "location": "San Francisco, Remote", "country": "", "description": "", "expect": {"keep": false, "reason": "family"}},
 {"id": "devops", "company": "hp", "role": "DevOps Engineer", "location": "Vancouver, Washington, United States of America", "country": "", "description": "", "expect": {"keep": false, "reason": "family"}},
 {"id": "firmware", "company": "hp", "role": "Embedded Firmware and Software Engineer", "location": "Spring Texas United States of America (2 Locations)", "country": "", "description": "", "expect": {"keep": false, "reason": "family"}},
 {"id": "soc-new-grad", "company": "nvidia", "role": "SoC Software Engineer - New College Grad 2027", "location": "US, CA, Santa Clara, US CA Santa Clara", "country": "", "description": "", "expect": {"keep": false, "reason": "family"}},
 {"id": "ai-engineer", "company": "workday", "role": "AI Engineer", "location": "USA, CA, Pleasanton, USA CA Pleasanton", "country": "", "description": "2+ years of experience building LLM applications with Python. Experience with RAG and agentic systems.", "expect": {"keep": true, "min_score": 40}},
 {"id": "no-description", "company": "workday", "role": "Senior Full Stack Software Engineer - Agent Platform", "location": "USA GA Atlanta (2 Locations)", "country": "", "description": "", "expect": {"keep": true, "tags": ["desc ?"]}},
 {"id": "cursor-range", "company": "cursor", "role": "Software Engineer, Model Lifecycle and Signals", "location": "New York", "country": "", "description": "We are looking for engineers with 3-5 years of experience in Python and TypeScript.", "expect": {"keep": true}},
 {"id": "crowdstrike-6-plus", "company": "crowdstrike", "role": "Sr. Backend Engineer II, AutoScaling & Balancing, NG-SIEM (Hybrid)", "location": "USA   Austin TX (4 Locations)", "country": "", "description": "6+ years of experience with Go or Java in distributed systems.", "expect": {"keep": false, "reason": "yoe"}},
 {"id": "itar", "company": "Example Co", "role": "Software Engineer", "location": "Sunnyvale, CA, us", "country": "us", "description": "2+ years of experience. Must be a U.S. citizen due to export control requirements (ITAR).", "expect": {"keep": false, "reason": "sponsorship"}},
 {"id": "preferred-then-required", "company": "Example Co", "role": "Senior Software Engineer, International", "location": "Menlo Park, CA; New York, NY", "country": "", "description": "Preferred qualifications:\n8+ years of experience\nMinimum qualifications:\n4+ years of experience with Python", "expect": {"keep": true}},
 {"id": "sponsor-yes", "company": "ServiceNow", "role": "Data Platform Software Engineer", "location": "Santa Clara, CALIFORNIA, us", "country": "us", "description": "3+ years of experience. Visa sponsorship is available.", "expect": {"keep": true}},
 {"id": "frontend-only", "company": "Example Co", "role": "Frontend Engineer", "location": "Seattle, WA", "country": "", "description": "", "expect": {"keep": false, "reason": "family"}},
 {"id": "senior-mts", "company": "Example Co", "role": "Senior Member of Technical Staff", "location": "San Francisco, CA", "country": "", "description": "5+ years of experience with TypeScript and React.", "expect": {"keep": true}}
]
```

- [ ] **Step 2: Write the test**

`tests/test_fixtures.py`:

```python
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
```

- [ ] **Step 3: Run**

Run: `.venv/bin/python -m pytest -q tests/test_fixtures.py`
Expected: `35 passed`.

- [ ] **Step 4: Ask the human to skim the labels (~2 min)**

Print a table they can eyeball:

Run: `.venv/bin/python -c "import json;[print(f\"{c['expect'].get('reason','KEEP'):12s} {c['role'][:60]:60s} {c['location'][:30]}\") for c in json.load(open('tests/fixtures/jobs.json'))]"`

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/jobs.json tests/test_fixtures.py
git commit -m "test: labeled job fixtures for the filter rules"
```

---

### Task 8: Fetchers return every fresh job with the new fields

**Files:**
- Modify: `pipeline/fetchers.py` (full replacement below; it keeps the Workday and SmartRecruiters fixes Gemini made on 2026-10-15)
- Modify: `config/boards.json` (remove `title_keywords`, `location_keywords`)
- Test: `tests/test_fetchers.py`

**Interfaces:**
- Produces: each fetcher returns dicts with `date_posted, company, role, location, yoe, job_link, stack, notes, source, extra_note, ats, job_id, country, description, detail_url`. `notes` is `source: <source>[; extra_note]`.
- Produces: fetchers drop the unused `cfg` parameter: `fetch_greenhouse(board, hours=30)`, `fetch_lever(company, hours=30)`, `fetch_ashby(board, hours=30)`, `fetch_smartrecruiters(company_id, hours=30)`, `fetch_workday(base_url, board_path, hours=30, limit=20)`.
- Removed: `matches_role`, `matches_location`, `_has_us`, `NON_US`, `US_STATES`.

- [ ] **Step 1: Write the failing test**

`tests/test_fetchers.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_fetchers.py`
Expected: failures, including `KeyError: 'ats'` and `test_no_filter_helpers_left`.

- [ ] **Step 3: Replace `pipeline/fetchers.py` with:**

```python
"""ATS job-board fetchers. Every function returns a list of normalized job dicts:

    {"date_posted", "company", "role", "location", "yoe", "job_link", "stack",
     "notes", "source", "extra_note", "ats", "job_id", "country",
     "description", "detail_url"}

Fetchers only apply the freshness window. Role / location / YOE filtering
lives in filters.py, driven by config/profile.json.

All public, unauthenticated JSON APIs. Polite: small sleep between boards.
Run verify_board(source, key) before adding any new board token.
"""
import html
import json
import re
import time
from datetime import datetime, timezone, timedelta

import requests

UA = {"User-Agent": "job-pipeline/1.0 (personal job search; contact in repo README)"}
TIMEOUT = 25
SLEEP_BETWEEN_BOARDS = 2

def load_config(path="config/boards.json"):
    with open(path) as f:
        return json.load(f)


def _parse_dt(value):
    """Parse ISO-8601-ish strings or epoch seconds/ms. Returns aware datetime or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:  # ms
            v /= 1000
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    # relative like "3 days ago" / "Posted 2 Days Ago"
    m = re.search(r"(\d+)\s+(day|hour|minute|week)", s, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"day": timedelta(days=n), "hour": timedelta(hours=n),
                 "minute": timedelta(minutes=n), "week": timedelta(weeks=n)}[unit]
        return datetime.now(timezone.utc) - delta
    if s.lower() in ("today", "just posted", "posted today"):
        return datetime.now(timezone.utc)
    if s.lower() in ("yesterday", "posted yesterday"):
        return datetime.now(timezone.utc) - timedelta(days=1)
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def html_to_text(s):
    """HTML (possibly entity-escaped, as Greenhouse sends it) to plain text,
    one line per block element."""
    s = html.unescape(s or "")
    s = re.sub(r"(?i)<\s*/?\s*(?:br|p|li|ul|ol|div|h[1-6]|tr)\b[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v\xa0]+", " ", s)
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())


def _norm(date_posted, company, role, location, job_link, source, extra_note="",
          *, ats="", job_id="", country="", description="", detail_url=""):
    notes = f"source: {source}"
    if extra_note:
        notes += f"; {extra_note}"
    return {
        "date_posted": date_posted,
        "company": company or "",
        "role": role or "",
        "location": location or "",
        "yoe": "",
        "job_link": job_link or "",
        "stack": "",
        "notes": notes,
        "source": source,
        "extra_note": extra_note,
        "ats": ats,
        "job_id": str(job_id or ""),
        "country": country or "",
        "description": description or "",
        "detail_url": detail_url or "",
    }


def _fresh(dt, hours):
    if not dt:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)


# ---------------------------------------------------------------- greenhouse
def fetch_greenhouse(board, hours=30):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                     params={"content": "true"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        pub_raw = j.get("first_published")
        dt = _parse_dt(pub_raw)
        verified = True
        if not dt:
            dt = _parse_dt(j.get("updated_at"))
            verified = False
        if not _fresh(dt, hours):
            continue
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            j.get("company_name") or board,
            title, loc,
            j.get("absolute_url", ""),
            f"greenhouse/{board}",
            "" if verified else "date=updated_at, spot-check",
            ats="greenhouse", job_id=j.get("id"),
            description=html_to_text(j.get("content") or ""),
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# --------------------------------------------------------------------- lever
def _lever_text(j):
    parts = [j.get("descriptionPlain") or ""]
    for block in j.get("lists") or []:
        parts.append(block.get("text") or "")
        parts.append(html_to_text(block.get("content") or ""))
    parts.append(j.get("additionalPlain") or "")
    return "\n".join(p for p in parts if p).strip()


def fetch_lever(company, hours=30):
    r = requests.get(f"https://api.lever.co/v0/postings/{company}",
                     params={"mode": "json"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):  # {"ok": false, ...} means bad slug
        raise ValueError(f"lever slug '{company}' invalid: {data}")
    out = []
    for j in data:
        dt = _parse_dt(j.get("createdAt"))
        if not _fresh(dt, hours):
            continue
        cats = j.get("categories") or {}
        title = j.get("text", "")
        loc = cats.get("location", "")
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            company, title, loc,
            j.get("hostedUrl") or j.get("applyUrl", ""),
            f"lever/{company}",
            ats="lever", job_id=j.get("id"), country=j.get("country") or "",
            description=_lever_text(j),
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# --------------------------------------------------------------------- ashby
def fetch_ashby(board, hours=30):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if not j.get("isListed", True):
            continue
        dt = _parse_dt(j.get("publishedAt"))
        if not _fresh(dt, hours):
            continue
        title = j.get("title", "")
        loc = j.get("location") or ""
        if j.get("isRemote"):
            loc = (loc + ", Remote").strip(", ")
        out.append(_norm(
            dt.strftime("%Y-%m-%d"),
            board, title, loc,
            j.get("jobUrl", ""),
            f"ashby/{board}",
            ats="ashby", job_id=j.get("id"),
            country=((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "",
            description=j.get("descriptionPlain") or "",
        ))
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# ------------------------------------------------------- smartrecruiters
def fetch_smartrecruiters(company_id, hours=30):
    out, offset, limit = [], 0, 100
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    while True:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings",
            params={"limit": limit, "offset": offset}, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data.get("content", [])
        if not items:
            break
        for j in items:
            dt = _parse_dt(j.get("releasedDate"))
            if not _fresh(dt, hours):
                # postings are newest-first; stop early once clearly old
                if dt and dt < cutoff - timedelta(days=2):
                    return out
                continue
            loc = j.get("location") or {}
            loc_s = ", ".join(x for x in
                              [loc.get("city"), loc.get("region"), loc.get("country")]
                              if x)
            title = j.get("name", "")
            link = (j.get("applyUrl")
                    or f"https://jobs.smartrecruiters.com/{company_id}/{j.get('id', '')}")
            out.append(_norm(
                dt.strftime("%Y-%m-%d"), company_id, title, loc_s,
                link, f"smartrecruiters/{company_id}",
                ats="smartrecruiters", job_id=j.get("id"), country=loc.get("country") or "",
                detail_url=("https://api.smartrecruiters.com/v1/companies/"
                            f"{company_id}/postings/{j.get('id', '')}"),
            ))
        if len(items) < limit:
            break
        offset += limit
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


# ------------------------------------------------------------------- workday
def fetch_workday(base_url, board_path, hours=30, limit=20):
    """base_url like https://nvidia.wd5.myworkdayjobs.com ; board_path like
    nvidia/NVIDIAExternalCareerSite. Endpoint shapes vary per tenant: verify first."""
    cxs_path = board_path.strip("/") if "/" in board_path else f"{board_path}/{board_path}"
    board_name = board_path.strip("/").split("/")[-1]
    company = board_path.strip("/").split("/")[0]
    url = f"{base_url.rstrip('/')}/wday/cxs/{cxs_path}/jobs"
    out, offset = [], 0
    while True:
        r = requests.post(url, json={"searchText": "", "appliedFacets": {},
                                     "limit": limit, "offset": offset},
                          headers={**UA, "Content-Type": "application/json"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data.get("jobPostings", [])
        if not items:
            break
        for j in items:
            dt = _parse_dt(j.get("postedOn") or j.get("startDate"))
            if not _fresh(dt, hours):
                continue
            title = j.get("title", "")
            loc = j.get("locationsText") or (j.get("bulletFields", [""])[0] if j.get("bulletFields") else "")
            ext_parts = j.get("externalPath", "").strip("/").split("/")
            if len(ext_parts) >= 2 and ext_parts[0] == "job":
                primary_loc = ext_parts[1].replace("-", " ")
                if not loc or "location" in loc.lower():
                    loc = f"{primary_loc} ({loc})" if loc else primary_loc
                elif primary_loc.lower() not in loc.lower():
                    loc = f"{loc}, {primary_loc}"
            link = f"{base_url.rstrip('/')}/{board_name}{j.get('externalPath', '')}"
            out.append(_norm(
                dt.strftime("%Y-%m-%d"), company, title, loc,
                link, f"workday/{board_path}",
                ats="workday", job_id=j.get("externalPath", ""),
                detail_url=f"{base_url.rstrip('/')}/wday/cxs/{cxs_path}{j.get('externalPath', '')}",
            ))
        if len(items) < limit or offset >= 200:
            break
        offset += limit
    time.sleep(SLEEP_BETWEEN_BOARDS)
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
}


def verify_board(source, key):
    """True if the board token returns a non-empty live job list."""
    try:
        if source == "greenhouse":
            d = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{key}/jobs",
                             headers=UA, timeout=TIMEOUT).json()
            return bool(d.get("jobs"))
        if source == "ashby":
            d = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{key}",
                             headers=UA, timeout=TIMEOUT).json()
            return bool(d.get("jobs"))
        if source == "lever":
            d = requests.get(f"https://api.lever.co/v0/postings/{key}",
                             params={"mode": "json", "limit": 1},
                             headers=UA, timeout=TIMEOUT).json()
            return isinstance(d, list) and bool(d)
        if source == "smartrecruiters":
            d = requests.get(f"https://api.smartrecruiters.com/v1/companies/{key}/postings",
                             params={"limit": 1}, headers=UA, timeout=TIMEOUT).json()
            return d.get("totalFound", 0) > 0
        return None  # workday shapes vary; verify manually per tenant
    except Exception:
        return False
```

- [ ] **Step 4: Remove the old keyword keys from `config/boards.json`**

Run: `.venv/bin/python -c "import json;p='config/boards.json';d=json.load(open(p));[d.pop(k,None) for k in ('title_keywords','location_keywords')];json.dump(d,open(p,'w'),indent=1);print(sorted(d))"`
Expected: `['_comment', 'ashby', 'greenhouse', 'lever', 'smartrecruiters', 'workday']`

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `184 passed`.

- [ ] **Step 6: Commit**

```bash
git add pipeline/fetchers.py config/boards.json tests/test_fetchers.py
git commit -m "feat(fetchers): return every fresh job with ats, id, country, description"
```

---

### Task 9: `discover.py` runs the filter pipeline

**Files:**
- Modify: `pipeline/discover.py` (full replacement)
- Test: `tests/test_discover.py`

**Interfaces:**
- Consumes: everything above, plus `add_job._db`, `add_job.normalize_url` and `add_job.DB_PATH`.
- Produces: `run_filters(jobs, prof, con, agent, fetch=fetch_detail, today=None) -> (kept, rejected, skipped)`. `kept` is finalized and sorted best first. `rejected` items are `{company, role, location, job_link, source, reason, detail}`.
- Produces: `init_rejected(con)`, `record_rejected(con, rejected, agent)`, `count_today(con, today=None) -> dict[str, int]`, `finalize(job)` and `format_yoe(lo, hi)`.
- Produces: the CLI gains `--profile`, `--db` and `--out-dir` (default `runs/`). It exits non-zero on a bad profile before any fetch. The summary JSON gains `rejected_file`, `already_known`, `kept` and `dropped`.

- [ ] **Step 1: Write the failing test**

`tests/test_discover.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_discover.py`
Expected: `ImportError: cannot import name 'count_today'`.

- [ ] **Step 3: Replace `pipeline/discover.py` with:**

```python
"""Driver: fetch fresh postings, filter them against config/profile.json,
write the kept and rejected jobs to runs/.

Usage:
    python discover.py --agent claude --sources greenhouse,ashby,lever
    python discover.py --agent gemini --sources workday,smartrecruiters

Writes: runs/candidates_<agent>_<YYYYMMDD-HHMM>.json  (kept, best score first)
        runs/rejected_<agent>_<YYYYMMDD-HHMM>.json    (dropped, with reason)
Prints a JSON summary to stdout.
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from add_job import DB_PATH, _db, normalize_url  # noqa: E402
from enrich import enrich_job, fetch_detail, init_cache, prune_cache  # noqa: E402
from fetchers import FETCHERS, load_config, verify_board  # noqa: E402
from filters import (cap, company_key, full_check, load_profile,  # noqa: E402
                     merge, quick_check, score)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------- storage
def init_rejected(con):
    con.execute("""CREATE TABLE IF NOT EXISTS rejected(
        url_key TEXT PRIMARY KEY, company TEXT, role TEXT, reason TEXT,
        detail TEXT, agent TEXT, first_seen TEXT, last_seen TEXT)""")


def record_rejected(con, rejected, agent):
    now = datetime.now().isoformat(timespec="seconds")
    for r in rejected:
        con.execute(
            """INSERT INTO rejected VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(url_key) DO UPDATE SET reason=excluded.reason,
               detail=excluded.detail, agent=excluded.agent, last_seen=excluded.last_seen""",
            (normalize_url(r["job_link"]), r["company"], r["role"], r["reason"],
             r["detail"], agent, now, now))


def count_today(con, today=None):
    """{company_key: rows appended today} from the seen registry."""
    today = today or date.today().isoformat()
    counts = Counter()
    for (company,) in con.execute(
            "SELECT company FROM seen WHERE substr(first_seen, 1, 10) = ?", (today,)):
        counts[company_key(company)] += 1
    return dict(counts)


def _skip_keys(con):
    """URLs already appended, or folded into another row by merge()."""
    keys = {r[0] for r in con.execute("SELECT url_key FROM seen")}
    try:
        keys |= {r[0] for r in con.execute(
            "SELECT url_key FROM rejected WHERE reason = 'merged'")}
    except sqlite3.Error as e:
        print(f"WARN: rejected log not readable: {e}", file=sys.stderr)
    return keys


# ---------------------------------------------------------------- filtering
def _rej(job, reason, detail):
    return {"company": job.get("company", ""), "role": job.get("role", ""),
            "location": job.get("location", ""), "job_link": job.get("job_link", ""),
            "source": job.get("source", ""), "reason": reason, "detail": detail or ""}


def format_yoe(lo, hi):
    if lo is None:
        return ""
    return f"{lo}–{hi}" if hi is not None else f"{lo}+"


def finalize(job):
    """Write the sheet-facing yoe / stack / notes fields."""
    job["yoe"] = format_yoe(job.get("yoe_min"), job.get("yoe_max"))
    job["stack"] = ", ".join(job.get("stack_terms", []))
    parts = [f"source: {job.get('source', '')}", f"score {job.get('score', 0)}",
             f"yoe {job['yoe']}" if job["yoe"] else "yoe ?",
             "sponsor ✓" if job.get("sponsorship") == "yes" else "sponsor ?"]
    parts += job.get("tags", [])
    if job.get("extra_note"):
        parts.append(job["extra_note"])
    job["notes"] = "; ".join(parts)
    return job


def run_filters(jobs, prof, con, agent, fetch=fetch_detail, today=None):
    """Returns (kept, rejected, skipped). kept is sorted best score first and
    finalized; rejected is a list of _rej dicts; skipped counts known URLs."""
    skip = _skip_keys(con)
    kept, rejected, skipped = [], [], 0
    for job in jobs:
        if normalize_url(job.get("job_link")) in skip:
            skipped += 1
            continue
        c = quick_check(job, prof)
        if not c.keep:
            rejected.append(_rej(job, c.reason, c.detail))
            continue
        job["tags"] = list(c.tags)
        enrich_job(job, prof, con=con, fetch=fetch)
        c = full_check(job, prof)
        if not c.keep:
            rejected.append(_rej(job, c.reason, c.detail))
            continue
        job["score"] = score(job, prof)
        kept.append(job)
    kept, folded = merge(kept)
    rejected += [_rej(j, "merged", link) for j, link in folded]
    kept, capped = cap(kept, prof, count_today(con, today))
    rejected += [_rej(j, "cap", detail) for j, detail in capped]
    try:
        record_rejected(con, rejected, agent)
        con.commit()
    except sqlite3.Error as e:  # the audit log must never block appends
        print(f"WARN: rejected log not written: {e}", file=sys.stderr)
    return [finalize(j) for j in kept], rejected, skipped


# --------------------------------------------------------------------- fetch
def fetch_all(cfg, sources, hours):
    jobs, dead, errors = [], [], []
    for src in sources:
        if src not in FETCHERS:
            errors.append(f"unknown source: {src}")
            continue
        boards = cfg.get(src, [])
        if src == "workday":
            boards = [b for b in boards if isinstance(b, dict)]  # {base_url, board_path}
        for key in boards:
            label = key if isinstance(key, str) else key.get("board_path", key)
            ok = verify_board(src, key if isinstance(key, str) else label)
            if ok is False:
                dead.append(f"{src}/{label}")
                continue
            try:
                if src == "workday":
                    jobs.extend(FETCHERS[src](key["base_url"], key["board_path"], hours=hours))
                else:
                    jobs.extend(FETCHERS[src](key, hours=hours))
            except Exception as e:  # per-board failure must not kill the run
                errors.append(f"{src}/{label}: {type(e).__name__}: {e}")
    return jobs, dead, errors


def _write(path, rows):
    with open(path, "w") as f:
        json.dump([{k: v for k, v in r.items() if k != "description"} for r in rows],
                  f, indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--sources", required=True,
                    help="comma-separated: greenhouse,lever,ashby,smartrecruiters,workday")
    ap.add_argument("--hours", type=int, default=30,
                    help="lookback window; 30h gives overlap between 6h runs (dedup handles it)")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "boards.json"))
    ap.add_argument("--profile", default=os.path.join(ROOT, "config", "profile.json"))
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "runs"))
    args = ap.parse_args()

    try:
        prof = load_profile(args.profile)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    cfg = load_config(args.config)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    jobs, dead, errors = fetch_all(cfg, sources, args.hours)

    con = _db(args.db)
    init_cache(con)
    init_rejected(con)
    prune_cache(con)
    kept, rejected, skipped = run_filters(jobs, prof, con, args.agent)
    con.close()

    os.makedirs(args.out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    cand_path = os.path.join(args.out_dir, f"candidates_{args.agent}_{ts}.json")
    rej_path = os.path.join(args.out_dir, f"rejected_{args.agent}_{ts}.json")
    _write(cand_path, kept)
    _write(rej_path, rejected)

    print(json.dumps({
        "candidates_file": cand_path,
        "rejected_file": rej_path,
        "fetched": len(jobs),
        "already_known": skipped,
        "kept": len(kept),
        "dropped": dict(Counter(r["reason"] for r in rejected).most_common()),
        "dead_boards": dead,
        "errors": errors,
    }, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `191 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/discover.py tests/test_discover.py
git commit -m "feat(discover): filter pipeline with rejected log and runs/ output"
```

---

### Task 10: `add_job.py`: best-first order and a Dropped column

**Files:**
- Modify: `pipeline/add_job.py`
- Test: `tests/test_add_job.py`

**Interfaces:**
- Produces: `RUNS_HEADER` gains `"Dropped"` (9 columns). `dropped_summary(input_path) -> str` returns e.g. `"level 39, family 18"`, `"none"` or `"-"`. `_ensure_runs_header(ws)` adds the column to an existing 8-column tab.

- [ ] **Step 1: Write the failing test**

`tests/test_add_job.py`:

```python
import json
import os
import sqlite3
import subprocess
import sys

from add_job import RUNS_HEADER, dropped_summary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADD_JOB = os.path.join(ROOT, "pipeline", "add_job.py")


def test_dropped_summary_reads_sibling_file(tmp_path):
    (tmp_path / "candidates_test_1.json").write_text("[]")
    (tmp_path / "rejected_test_1.json").write_text(json.dumps(
        [{"reason": "level"}, {"reason": "level"}, {"reason": "yoe"}]))
    assert dropped_summary(str(tmp_path / "candidates_test_1.json")) == "level 2, yoe 1"


def test_dropped_summary_without_sibling(tmp_path):
    (tmp_path / "candidates_test_2.json").write_text("[]")
    assert dropped_summary(str(tmp_path / "candidates_test_2.json")) == "-"
    assert dropped_summary(str(tmp_path / "other.json")) == "-"


def test_runs_header_has_dropped():
    assert RUNS_HEADER[-1] == "Dropped" and len(RUNS_HEADER) == 9


def _run(tmp_path, rows):
    inp = tmp_path / "candidates_test_x.json"
    inp.write_text(json.dumps(rows))
    db = tmp_path / "seen.db"
    r = subprocess.run([sys.executable, ADD_JOB, "--agent", "test", "--input", str(inp),
                        "--skip-sheet", "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), sqlite3.connect(db)


def test_old_format_candidates_still_work(tmp_path):
    old = [{"date_posted": "2026-10-14", "company": "Datadog", "role": "Software Engineer",
            "location": "New York, NY", "yoe": "", "job_link": "https://x/1", "stack": "",
            "notes": "source: greenhouse/datadog"}]
    summary, _ = _run(tmp_path, old)
    assert summary["appended"] == 1


def test_appends_best_score_first(tmp_path):
    rows = [{"company": c, "role": "Software Engineer", "location": "NY",
             "job_link": f"https://x/{c}", "notes": "source: t", "score": s}
            for c, s in (("low", 10), ("high", 90), ("mid", 50))]
    _, con = _run(tmp_path, rows)
    order = [r[0] for r in con.execute("SELECT company FROM seen ORDER BY rowid")]
    assert order == ["high", "mid", "low"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_add_job.py`
Expected: `ImportError: cannot import name 'dropped_summary'`.

- [ ] **Step 3: Edit `pipeline/add_job.py`. These are the only five changes.**

(a) Imports: add `from collections import Counter` under `import sys`.

(b) Header constant:

```python
RUNS_HEADER = ["Timestamp", "Agent", "Sources", "Fetched",
               "Appended", "Dup URL", "Dup content", "Errors", "Dropped"]
```

(c) Add above `def load_seen_from_sheet(sh):`

```python
def _ensure_runs_header(ws):
    """Older Pipeline Runs tabs have 8 columns; add the Dropped header."""
    n = len(RUNS_HEADER)
    if ws.col_count < n:
        ws.add_cols(n - ws.col_count)
    if ws.cell(1, n).value != RUNS_HEADER[-1]:
        ws.update_cell(1, n, RUNS_HEADER[-1])


def dropped_summary(input_path):
    """'level 39, family 18, ...' from the sibling rejected_*.json, '-' if none."""
    folder, name = os.path.split(input_path)
    if not name.startswith("candidates_"):
        return "-"
    path = os.path.join(folder, "rejected_" + name[len("candidates_"):])
    try:
        with open(path) as f:
            rejected = json.load(f)
    except (OSError, ValueError):
        return "-"
    counts = Counter(r.get("reason") or "?" for r in rejected)
    return ", ".join(f"{k} {v}" for k, v in counts.most_common()) or "none"
```

(d) In `main()`, right after `candidates = json.load(f)`:

```python
    # best first; older candidate files have no score and keep their order
    candidates.sort(key=lambda c: -(c.get("score") or 0))
```

(e) In the runs-tab block, call `_ensure_runs_header(runs)` right after `runs = _ensure_tab(sh, RUNS_TAB, RUNS_HEADER)`, and append the new value to the row:

```python
             summary["dup_url"], summary["dup_content"], summary["errors"],
             dropped_summary(args.input)],
            value_input_option="RAW")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `196 passed`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/add_job.py tests/test_add_job.py
git commit -m "feat(add_job): append best score first, log drops per reason"
```

---

### Task 11: Agent prompts and README

**Files:**
- Modify: `prompts/claude.md`, `prompts/gemini.md`, `README.md`

No code and no tests. A reviewer checks that the paths match `discover.py` output (`runs/…`).

- [ ] **Step 1: `prompts/claude.md`: replace steps 1–3 with**

```markdown
1. **Discover.** Run:
   `python3 pipeline/discover.py --agent claude --sources greenhouse,ashby,lever`
   This verifies every board token, fetches postings from the last 30 hours
   (the 6h overlap is intentional; dedup handles it), applies the candidate
   profile in `config/profile.json` (role, level, YOE, sponsorship, US only,
   max 3 per company per day), and writes `runs/candidates_claude_<timestamp>.json`
   (kept, best score first) and `runs/rejected_claude_<timestamp>.json`.
   It prints a JSON summary including `dropped`, `dead_boards` and `errors`.

2. **Spot-check.** Open the candidates file and sanity-check 2-3 entries:
   dates within the window, roles fit the profile, locations in the US, Notes
   show a score. Then read 3 random entries in the rejected file. If a job that
   clearly fits was dropped, or a misfit was kept, report its title, location
   and reason. Do NOT edit `config/profile.json`, `pipeline/filters.py` or
   `pipeline/enrich.py`; the human tunes those.
   If an API changed shape and a fetcher is mis-parsing, fix
   `pipeline/fetchers.py` minimally and re-run discovery for the affected
   source. Note the fix in your final report.

3. **Append — through the gate, never directly.**
   `python3 pipeline/add_job.py --agent claude --input runs/candidates_claude_<timestamp>.json`
   `add_job.py` is the ONLY thing allowed to write to the sheet. It dedupes
   against both job tabs and a local registry, appends only genuinely new jobs
   to the "Pipeline Jobs" tab, and logs the run to "Pipeline Runs". Never call
   the Sheets API yourself. Never edit or delete existing rows. Append-only.
```

Add this to **Hard rules**:

```markdown
- Never edit `config/profile.json`, `pipeline/filters.py` or `pipeline/enrich.py`.
```

Replace the **Report** sentence with:

```markdown
End every run with a short report: fetched / kept / appended / dupes /
dropped by reason / errors, dead boards, any fetcher fixes, any new boards
added, and any filter mistakes you spotted in step 2.
```

- [ ] **Step 2: `prompts/gemini.md`: same three edits**

Make the same three edits, with `--agent gemini --sources workday,smartrecruiters` and `runs/candidates_gemini_<timestamp>.json`. Leave step 4 (tenant expansion) unchanged.

- [ ] **Step 3: `README.md`**

Make three replacements:
- `python3 pipeline/add_job.py --agent test --input candidates_test_*.json --dry-run` becomes `python3 pipeline/add_job.py --agent test --input runs/candidates_test_*.json --dry-run`.
- `python3 pipeline/add_job.py --agent test --input candidates_test_*.json` becomes `python3 pipeline/add_job.py --agent test --input runs/candidates_test_*.json`.
- `- Role filter: \`config/boards.json\` \`title_keywords\` — edit freely.` becomes `- Filters: \`config/profile.json\` (role, level, YOE, sponsorship, location, stack score, per-company cap). Agents must not edit it. Spec: \`docs/superpowers/specs/2026-10-15-job-filters-design.md\`.`

- [ ] **Step 4: Commit**

```bash
git add prompts/claude.md prompts/gemini.md README.md
git commit -m "docs: agent prompts and README for the filter pipeline"
```

---

### Task 12: Live dry run, human review, go live

**Files:** none changed. This task produces evidence.

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: `196 passed`.

- [ ] **Step 2: Discover against all boards (no sheet writes)**

Run: `.venv/bin/python pipeline/discover.py --agent test --sources greenhouse,ashby,lever,smartrecruiters,workday`
Expected: a summary with `kept` well below `fetched`, a `dropped` map, and `errors: []`. It takes about 2–4 minutes.

- [ ] **Step 3: Gate dry run**

Run: `.venv/bin/python pipeline/add_job.py --agent test --input runs/candidates_test_<ts>.json --dry-run`
Expected: `appended` equals `kept` minus jobs already in the sheet.

- [ ] **Step 4: Show the human the kept list and a rejected sample**

Run: `.venv/bin/python -c "import json,glob;f=sorted(glob.glob('runs/candidates_test_*.json'))[-1];[print(c['notes'].split(';')[1].strip(), '|', c['company'], '|', c['role'][:60], '|', c['yoe'] or '?', '|', c['stack']) for c in json.load(open(f))]"`

Run: `.venv/bin/python -c "import json,glob,random;f=sorted(glob.glob('runs/rejected_test_*.json'))[-1];r=json.load(open(f));random.seed(1);[print(x['reason'], '|', x['detail'][:30], '|', x['role'][:60]) for x in random.sample(r, min(15, len(r)))]"`

Stop and wait for the human to say the lists look right (about 5 minutes). Tune `config/profile.json` if they ask, re-run Step 1, then repeat Steps 2–4.

- [ ] **Step 5: Go live (only after approval)**

Run: `./run_claude.sh` then `./run_gemini.sh`
Expected: new rows in "Pipeline Jobs" with YOE / Stack / Notes filled, and a "Pipeline Runs" row whose `Dropped` column has counts.
