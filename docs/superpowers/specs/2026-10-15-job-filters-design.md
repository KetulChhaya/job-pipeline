# Job filters + enrichment: design

Date: 2026-10-15
Status: approved in chat, pending written-spec review
Spec 1 of 3 (2 = pipeline hardening, 3 = source expansion)

## Goal

Only jobs that fit the candidate profile reach the "Pipeline Jobs" tab, and
every appended row carries YOE, a stack summary, and a fit score so Muse can
apply best-first.

Baseline (2026-10-15, 100 unique candidates from 54 boards): 44 Senior,
28 Staff/Principal/Lead, 7 Manager, 4 new grad, 17 mid/unlabeled; NVIDIA
alone was 37; 12 were hardware/IT-adjacent; 2 were non-US; YOE and Stack
columns were empty on all 100.

## Success criteria

1. Every labeled fixture in `tests/fixtures/jobs.json` gets the expected
   keep/drop and reason.
2. A dry run against the current boards appends zero jobs that are
   Staff+/Manager/new-grad, non-US, in an excluded family, asking >5 YOE,
   or stating no sponsorship.
3. Every appended row has `Stack summary` filled when a description was
   available, and `YOE` filled when the description states one.
4. No company gets more than 3 appended rows per calendar day.
5. At today's board count, a run takes at most ~2 minutes longer than now
   (detail requests only for jobs that pass the quick check).

## Non-goals (later specs)

- Spec 2: hourly deterministic cron (no LLM in the hot path), parallel
  fetching, lock around `add_job.py`, registry-commit-before-append bug,
  content-key collisions, moving `service-account.json` out of agent reach.
- Spec 3: slug harvest (~2,800 boards), Amazon / Workable / Rippling / Gem
  fetchers, company display names, Workday `searchText` and paging.
- Later: H-1B filing-history boost, LLM pass over "?" jobs, liveness checks.
- Existing rows in the sheet are not touched (append-only).

## Candidate profile

Stated by the user:
- Experience: 2–5 years.
- Role families: backend, full-stack, AI engineer / applied AI (LLM
  integration, agent development, Bedrock, RAG).
- Stack (ranking only): TypeScript / Node / React, Python.
- Needs visa sponsorship.

Assumed (approved, all configurable in `config/profile.json`):
- Pure frontend, mobile, data engineering, DevOps/SRE, and
  systems/embedded/hardware roles are dropped.
- Classic ML (recsys, personalization, training) is dropped unless the
  description mentions LLM / RAG / agents / GenAI / Bedrock.
- US only, any metro, any work mode.
- New-grad roles are dropped.

## Data flow

```
fetchers.py   fetch, freshness only (posted <= 30h)
   |          adds job_id, ats, country (if structured), description (if in list)
   v
filters.quick_check   title + location: family, level, country      -> drop + reason
   v
enrich.py     description for survivors (cache-first), extract yoe/stack/sponsorship
   v
filters.full_check    YOE, sponsorship/clearance, ML-needs-applied-AI -> drop + reason
   v
filters.score -> filters.merge (same company+title) -> filters.cap (3/company/day)
   v
runs/candidates_<agent>_<ts>.json   (kept, sorted by score desc)
runs/rejected_<agent>_<ts>.json     (dropped, with reason + detail)
   v
add_job.py    dedup gate (unchanged logic) -> sheet, YOE/Stack/Notes filled
```

Principle: if a check cannot decide (no description, no YOE stated, no
sponsorship wording, ambiguous remote), the job is **kept and tagged `?`**,
never silently dropped.

## Components

| File | Status | Responsibility |
|---|---|---|
| `config/profile.json` | new | All filter settings. Agents must not edit it. |
| `pipeline/filters.py` | new | Pure functions: `quick_check(job, profile)`, `full_check(job, profile)`, `score(job, profile)`, `merge(jobs)`, `cap(jobs, profile, already_today)`. Each check returns `(keep: bool, reason: str \| None, detail: str \| None)`. |
| `pipeline/enrich.py` | new | `get_description(job)` (cache-first), `html_to_text`, `extract_yoe`, `extract_stack`, `extract_sponsorship`. No filtering decisions. |
| `pipeline/fetchers.py` | changed | Remove `matches_role` / `matches_location` calls from fetchers. Add fields below. Keep the Workday/SmartRecruiters fixes Gemini made on 2026-10-15. |
| `pipeline/discover.py` | changed | Orchestrates the flow above, writes both files under `runs/`, summary JSON gains `dropped: {reason: count}`. |
| `pipeline/add_job.py` | changed | Fills YOE / Stack summary / Notes, appends in score order, reads the sibling `rejected_*.json` for per-reason counts in Pipeline Runs. |
| `config/boards.json` | changed | `title_keywords` / `location_keywords` move to `profile.json`. Boards only. |
| `prompts/claude.md`, `prompts/gemini.md` | changed | Agents never edit `profile.json`, `filters.py`, `enrich.py`. Spot-check covers rejected file too. |

### Job dict (fetcher output)

Existing fields plus:

| Field | Meaning |
|---|---|
| `ats` | `greenhouse` / `ashby` / `lever` / `smartrecruiters` / `workday` |
| `job_id` | ATS-native id (Workday: `externalPath`) |
| `source` | e.g. `greenhouse/stripe` (replaces parsing `notes.split(";")[0]`) |
| `country` | Structured country if the ATS gives it, else `""` |
| `description` | Plain text if the list endpoint includes it, else `""` |

### Where descriptions come from (verified 2026-10-15)

| ATS | Description | Country |
|---|---|---|
| Greenhouse | list endpoint with `?content=true` (HTML-escaped HTML; `html.unescape` then strip tags). Larger payload, accepted. | none (text fallback) |
| Ashby | list: `descriptionPlain` | `address.postalAddress.addressCountry` |
| Lever | list: `descriptionPlain` + `lists` + `additionalPlain` | `country` (ISO-2) |
| SmartRecruiters | detail: `GET /v1/companies/{id}/postings/{postingId}` -> `jobAd.sections` | list: `location.country` |
| Workday | detail: `GET {base}/wday/cxs/{tenant}/{board}{externalPath}` -> `jobPostingInfo.jobDescription` | detail: `jobPostingInfo.country.descriptor` |

Detail requests run only for jobs that pass `quick_check`, honor the
existing per-board sleep, and are cached.

## Rules

### quick_check (title + location)

Order: location, family, level, citizenship. First failing rule is the reason.

1. **Location** (`reason: location`)
   - Structured `country` present and not US -> drop.
   - Else text fallback, fixing the current bugs:
     - Two-letter state codes match case-sensitively on the original
       string (`CA`, not `ca`). `IN` counts only alongside another US
       signal (`US`, `USA`, `United States`, a US city/state name).
     - Expanded non-US list (adds Israel, Tel Aviv, Portugal, Lisbon,
       Romania, Serbia, Argentina, Colombia, Chile, Philippines, Vietnam,
       China, Korea, Taiwan, Czech, Sweden, Switzerland, Italy, Belgium,
       Denmark, Finland, Norway, Austria, Ukraine, Turkey, Egypt, Nigeria,
       Kenya, South Africa, UAE, Dubai, Pakistan, New Zealand, ...).
     - Non-US name present and no US signal -> drop.
     - `remote` with no country, or `global` / `worldwide` / `anywhere`
       -> keep, tag `loc ?`.
2. **Family** (`reason: family`)
   - Title must match a keep pattern: software engineer / engineering /
     developer,
     software development engineer, SDE, member of technical staff, MTS,
     backend / back end / back-end, full stack / fullstack / full-stack,
     AI engineer, applied AI, LLM engineer, GenAI / generative AI engineer,
     agent(s) engineer, product engineer, API engineer, machine learning /
     ML engineer (conditional, see full_check).
   - Drop patterns win over keep patterns: `system(s) software`, SoC,
     firmware, embedded, kernel, driver, CUDA, ASIC, FPGA, silicon, EDA,
     validation engineer, verification engineer, design verification,
     system validation, hardware, mfg / manufacturing, security
     engineer, cybersecurity, sales / solutions / support engineer,
     solutions architect, DevOps, SRE / site reliability, data engineer,
     mobile, iOS, Android, frontend / front end / front-end (unless
     "full" also appears), client platform, network engineer,
     `platform engineer` not preceded by `software`.
   - Word boundaries matter: `software engineer` must not require an exact
     word end (so `Software Engineering MTS` matches), and narrow drops like
     `verification engineer` avoid hitting product teams such as
     "Software Engineer, Identity Verification".
3. **Level** (`reason: level`)
   - Drop: staff, principal, lead, architect, distinguished, fellow,
     manager, director, head of, VP, intern, new grad, college grad,
     graduate, university, AMTS, LMTS, PMTS, `staff+`.
   - Senior markers (senior, sr, SMTS) -> pass here, flagged
     `senior=True` for full_check.
4. **Citizenship markers in title** (`reason: sponsorship`, only when
   `needs_sponsorship`): GovCloud, cleared, clearance, TS/SCI, secret.

### full_check (description)

1. **YOE** (`reason: yoe`)
   - Split description into sentences. Ignore sentences containing
     preferred / bonus / nice to have / a plus / ideally.
   - Only sentences containing "experience" count.
   - Patterns: `N+ years`, `N-M years` / `N–M` / `N to M`, `at least N`,
     `minimum (of) N`, `N or more years`, number words one..fifteen.
   - `yoe_min` = largest minimum found; `yoe_max` = its paired max if any.
   - `yoe_min > 5` -> drop.
   - `senior=True` and `yoe_min` unknown -> keep, tag `yoe ?`
     (knob: `senior_unknown_yoe: "keep" | "drop"`, default `keep`).
2. **Sponsorship / clearance** (`reason: sponsorship`)
   Negative patterns checked first. Any match -> drop.
   - `(unable|not able|cannot|can't|will not|won't|do not|does not|are not able) (to )?(provide |offer )?(visa )?sponsor`
   - `without (the need for )?(current or future |now or in the future )?(visa |employment )?sponsorship`
   - `no (visa )?sponsorship`, `sponsorship (is )?not (available|offered|provided)`
   - `must be a u.s. citizen`, `u.s. citizen(ship)? (is )?(required|only)`, `us citizens only`, `green card holder`
   - clearance: `(active|current|ability to obtain|eligible for|must (obtain|hold|possess)) ... clearance`, `ts/sci`, `top secret`, `secret clearance`
   - `itar`, `u.s. person`
   - Positive (tag `sponsor ✓`): `(will|can|able to) sponsor`,
     `visa sponsorship (is )?available`, `h-?1b (sponsorship|transfer) (is )?(available|supported)`.
   - Neither -> tag `sponsor ?`.
3. **ML needs applied AI** (`reason: family`, detail `ml-not-applied-ai`)
   - Title is ML / machine learning engineer and description has none of:
     LLM, large language model, RAG, retrieval-augmented, agent, agentic,
     GenAI, generative AI, Bedrock, LangChain, prompt -> drop.
   - No description -> keep, tag `desc ?`.

### score (0–100, weights in profile.json)

| Component | Points |
|---|---|
| User stack, 5 groups x 10: TypeScript/JavaScript; Node; React/Next.js; Python; FastAPI/Django/Flask | 0–50 |
| Applied AI, 5 groups x 5: LLM/GenAI; RAG/embeddings/vector DB; agents/agentic; Bedrock/LangChain/OpenAI/Anthropic; prompt | 0–25 |
| YOE known and <= 5 -> 15; unknown -> 8 | 8–15 |
| `sponsor ✓` -> 10; `sponsor ?` -> 5 | 5–10 |

### merge

Same normalized company + normalized title in one run -> one row: highest
score wins; location becomes `A; B (+N more)`; link of the winner.

### cap

`per_company_per_day: 3`. Count = rows already appended today for that
normalized company (from `seen.db`, `date(first_seen) = today`) + rows
kept this run. Over the cap -> `reason: cap`, lowest scores dropped first.
Company normalization: lowercase, strip `inc`, `llc`, `ltd`, punctuation.

## Sheet output

Columns unchanged (same 9 as the LinkedIn tab):

| Column | Value |
|---|---|
| YOE | `3–5`, `5+`, or blank |
| Stack summary | up to 6 terms, user stack first, e.g. `Python, TypeScript, React, AWS, RAG` |
| Notes | `source: greenhouse/stripe; score 78; yoe 3–5; sponsor ?` (source stays first so existing parsing and rows stay compatible) |

Rows are appended in score order, best first.

Pipeline Runs gains a 9th column **Dropped**:
`level 39, family 18, yoe 12, sponsorship 3, cap 20, location 2`.
If the existing header row lacks it, `add_job.py` writes `Dropped` to I1.

Stack dictionary (for the summary, ~50 terms): languages (Python,
TypeScript, JavaScript, Java, Kotlin, Go via `golang` or case-sensitive
`Go`, Rust, C++, C#, Ruby, Scala), frameworks (React, Next.js, Node.js,
Express, NestJS, Django, FastAPI, Flask, Spring, Rails, GraphQL, .NET),
cloud/infra (AWS, GCP, Azure, Kubernetes, Docker, Terraform, Lambda),
data (Postgres, MySQL, MongoDB, Redis, Kafka, Spark, Snowflake, DynamoDB),
AI (LLM, RAG, agents, Bedrock, LangChain, OpenAI, Anthropic/Claude,
embeddings, vector DB, pgvector, Pinecone, PyTorch).

## Storage (seen.db)

Owned by `discover.py` / `enrich.py` (`add_job.py` keeps owning `seen`):

```sql
CREATE TABLE IF NOT EXISTS desc_cache(
  url_key TEXT PRIMARY KEY, description TEXT, country TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS rejected(
  url_key TEXT PRIMARY KEY, company TEXT, role TEXT, reason TEXT,
  detail TEXT, agent TEXT, first_seen TEXT, last_seen TEXT);
```

- `desc_cache` rows older than 3 days are pruned at run start.
- `rejected` upserts on `url_key` (updates `last_seen`, `reason`).
- Run files go to `runs/` (created if missing). Old root-level
  `candidates_*.json` files are left alone.

## config/profile.json (shape)

```json
{
  "max_yoe": 5,
  "senior_unknown_yoe": "keep",
  "per_company_per_day": 3,
  "needs_sponsorship": true,
  "location": {"countries": ["US"], "non_us_terms": ["..."], "ambiguous_remote": "keep"},
  "family": {"keep": ["..."], "drop": ["..."], "ml_titles": ["machine learning engineer", "ml engineer"],
             "ml_requires_any": ["llm", "rag", "agent", "genai", "bedrock", "langchain", "prompt"]},
  "level": {"drop": ["..."], "senior": ["senior", "sr", "smts"]},
  "sponsorship": {"negative": ["..."], "positive": ["..."]},
  "stack": {"user_groups": {"typescript": ["typescript", "javascript"], "...": []},
            "ai_groups": {"llm": ["llm", "genai", "generative ai"], "...": []},
            "dictionary": ["..."]},
  "weights": {"user_stack_per_group": 10, "ai_per_group": 5,
              "yoe_known": 15, "yoe_unknown": 8, "sponsor_yes": 10, "sponsor_unknown": 5}
}
```

`"..."` means the full lists from the Rules section. Patterns are
lowercase regex fragments matched with word boundaries unless noted
(state codes and `Go` are case-sensitive).

If `needs_sponsorship` is false, sponsorship patterns tag only; clearance
still drops.

## Error handling

| Situation | Behavior |
|---|---|
| Description fetch fails / times out | keep, tag `desc ?`, no cache write |
| `profile.json` missing or invalid JSON / missing keys | `discover.py` exits non-zero before fetching, clear message |
| `rejected` table write fails | log to stderr, continue; appends unaffected |
| `rejected_*.json` sibling missing | `add_job.py` writes `-` in Dropped, continues |
| Regex error in a profile pattern | fail at load time (compile all patterns once) |

## Testing

`pytest` (added to `requirements-dev.txt`).

1. `tests/fixtures/jobs.json`: ~30 real postings captured 2026-10-15
   (title, location, country, description) with expected
   `keep` / `reason`. Must include: `Bengaluru, Karnataka, IN`,
   `Remote, Israel`, `London or Remote`, `Remote, Global` (keep + `loc ?`),
   Software Engineering Manager, AMTS (College Grad), Senior with
   "5+ years" (keep), Senior with "7+ years" (drop), "unable to sponsor",
   Salesforce GovCloud (drop, `sponsorship`), "Software Engineer, Identity
   Verification" (keep), "Software Engineering MTS" (keep), Spotify ML
   Personalization (drop), ML role with
   LLM/RAG (keep), NVIDIA System Software Engineer (drop), Workday
   "Sr Software Engineer – API Platform (Python/Typescript)" (keep, high
   score). The user reviews the label list once (~2 min).
2. `tests/test_yoe.py`: "3+ years", "3-5 years", "3–5 years",
   "three or more years", "minimum of 4 years", "at least 5 years",
   "5+ years overall, 2+ years with React" (-> 5), preferred-only line
   ignored, "founded 10 years ago" ignored.
3. `tests/test_sponsorship.py`: each negative/positive pattern, plus
   "we are able to sponsor" (positive) vs "not able to sponsor" (negative).
4. `tests/test_location.py`: the fallback cases above plus multi-location
   strings with one US entry (keep).
5. `tests/test_cap_merge.py`: merge of same title across locations; cap
   counting today's existing rows.

## Rollout

1. Implement, tests green.
2. Dry run against current boards:
   `python3 pipeline/discover.py --agent test --sources greenhouse,ashby,lever,smartrecruiters,workday`
   then `python3 pipeline/add_job.py --agent test --input runs/candidates_test_*.json --dry-run`.
   Print kept list with scores and the per-reason drop counts.
3. User reviews kept + a sample of rejected (~5 min).
4. Go live: update both prompts, next scheduled runs use it.

Estimated build time: 5–6 hours.

## Open knobs (defaults chosen, change in profile.json)

- `senior_unknown_yoe`: `keep` (tag `yoe ?`).
- `ambiguous_remote`: `keep` (tag `loc ?`).
- `per_company_per_day`: 3.
- Frontend-only roles: dropped.
