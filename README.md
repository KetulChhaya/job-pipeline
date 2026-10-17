# Job Discovery Pipeline

Finds software-engineering jobs posted in the last ~30 hours, keeps the ones that
fit **your** profile (role, level, years of experience, sponsorship, US only), and
appends them to **your** Google Sheet, best match first. One command runs it:
`jobhunt`.

```
jobhunt ─┬─ Claude lane  (claude CLI)  ── Greenhouse · Ashby · Lever ─────────┐
         ├─ Gemini lane  (agy CLI)     ── Workday · SmartRecruiters ──────────┼─ discover.py ─ add_job.py ─ Google Sheet
         └─ Feeds lane   (no LLM)      ── SimplifyJobs · speedyapply · jobright┘   (filters)    (dedup gate)
```

- Each lane fetches fresh postings, filters them against `config/profile.json`,
  fetches descriptions for YOE / sponsorship checks, scores them, and hands them to
  `pipeline/add_job.py`, the only thing that writes to the sheet. The same job is
  never appended twice, even when two lanes or two sites list it.
- The LLM lanes are optional. With no `claude` / `agy` CLI you still get the feeds lane.
- Architecture diagram: `docs/architecture/job-pipeline.excalidraw` (open in excalidraw.com).

## Setup (~15 min)

### 1. A Google service account (the pipeline writes as this account)

1. [Google Cloud Console](https://console.cloud.google.com) → create a project (any name).
2. APIs & Services → Library → **Google Sheets API** → Enable.
3. APIs & Services → Credentials → Create Credentials → **Service account** (any name).
4. Open it → Keys → Add Key → **JSON** → download. Keep it outside git
   (`service-account.json` in this folder is git-ignored).

### 2. A Google Sheet

Create one (or use yours) and **Share** it with the service account's email
(`…@….iam.gserviceaccount.com`, inside the JSON key) as **Editor**. The pipeline
creates its tabs on the first run.

### 3. Run the wizard

```bash
./setup.sh
```

It creates `.venv`, asks for the sheet URL, the key path and which lanes to enable
(it checks whether `claude` / `agy` are installed), writes `config/settings.json`,
copies `config/profile.example.json` → `config/profile.json` and the boards list,
adds the `jobhunt` alias, and does a read-only check that the key can open the sheet.

### 4. Make the profile yours

Edit `config/profile.json`: at least `max_yoe`, `needs_sponsorship`, `family.keep`
(which role types you want) and `stack.user_groups` / `stack.user_terms` (your stack,
used only for ranking).

### 5. Try it safely, then for real

```bash
jobhunt menu      # pick 5: feeds dry run, no sheet writes
jobhunt           # every enabled lane, writes to your sheet
```

## Running it

| Command | Does |
|---|---|
| `jobhunt` | every enabled lane, then one total line + one macOS notification |
| `jobhunt feeds` · `jobhunt claude feeds` | only those lanes, in that order |
| `jobhunt menu` | numbered menu: lanes (several at once, e.g. `1 3`), feeds dry run, open the latest log |
| `./run_claude.sh` · `./run_gemini.sh` · `./run_feeds.sh` | one lane |

While a lane runs, `·` lines come from the pipeline (per board, filter, append) and
`›` lines are the agent's narration. After each lane: the top 25 jobs it appended
(score, company, role, YOE, link), the agent's report, and the log path with a ready
`less +G <log>` command.

## Settings

Personal files are git-ignored: `config/settings.json`, `config/profile.json`,
`config/boards.json`. The repo only ships `*.example.json` versions.

`config/settings.json` (every key optional except `sheet.id`; defaults shown):

| Key | Default | Meaning |
|---|---|---|
| `sheet.id` | — | sheet URL or ID to append to |
| `sheet.jobs_tab` / `sheet.runs_tab` | `Pipeline Jobs` / `Pipeline Runs` | tabs the pipeline writes |
| `sheet.dedup_tabs` | `["LinkedIn Easy Apply"]` | other tabs whose jobs count as "already have it" (`[]` for none) |
| `service_account` | `service-account.json` | key path (relative to this folder, or absolute) |
| `lanes.claude.enabled` / `.model` / `.effort` | `true` / `claude-sonnet-5-5` / `high` | Claude lane |
| `lanes.gemini.enabled` / `.model` / `.retries` / `.retry_wait` | `true` / `gemini-3.8-flash-medium` / `3` / `120` | Gemini (agy) lane |
| `lanes.feeds.enabled` | `true` | GitHub feeds lane |
| `fetch.hours` | `30` | how far back a posting counts as fresh |
| `fetch.recent_title_days` | `3` | skip a job whose company + title was appended this many days ago |
| `fetch.timeout` / `board_sleep` / `detail_sleep` | `25` / `2` / `1` | seconds; politeness toward the job sites |
| `fetch.contact` | `""` | your email in the User-Agent, so sites can reach you |

Environment overrides: `JOB_SETTINGS` (use another settings file), `JOB_SHEET_ID`,
`GOOGLE_SERVICE_ACCOUNT`, `JOB_NOTIFY=0` (no notifications).
Read a value from the shell: `python3 pipeline/settings.py get lanes.claude.model`.

`config/profile.json` holds the filter rules (role family, level, YOE, sponsorship,
location, stack scoring, max 3 jobs per company per day). The agents must not edit it.
`config/boards.json` lists the job boards; the agents may add boards they've verified.

## Sharing this with a friend

Share a clean copy, without git history or your personal files:

```bash
git archive HEAD -o job-pipeline.zip
```

`git archive` leaves out everything git-ignored (your settings, profile, boards, key,
`seen.db`, logs) **and** the history. Don't share the repo itself: older commits still
contain an earlier `config/profile.json`. Your friend unzips it and runs `./setup.sh`.

## How dedup works

1. Every run rebuilds the "already have it" set from the jobs tab plus `sheet.dedup_tabs`.
2. Link key: tracking params (`utm_*`, `gclid`, …) stripped, ATS ids (`gh_jid`, …) kept.
3. Content key: normalized `company | role | location` catches one posting mirrored on two sites.
4. Same company + title appended in the last `fetch.recent_title_days` days → skipped.
5. `pipeline/seen.db` (SQLite) remembers every appended job, cached descriptions, and
   every rejected job with its reason.

## Monitoring and troubleshooting

- **Pipeline Runs** tab: one row per lane run (fetched, appended, dupes, errors, and
  `Dropped` counts per reason). `logs/`: one readable log per lane run.
- Check the key and sheet without writing anything: `.venv/bin/python pipeline/add_job.py --agent check --check`.
- `no sheet configured`: set `sheet.id` in `config/settings.json`, or run `./setup.sh`.
- `service account key not found`: fix `service_account` in `config/settings.json`.
- `403` from Sheets: the sheet isn't shared with the service-account email (Editor).
- `! jobright: security check`: jobright.ai is rate-limiting you; its jobs arrive tagged
  `desc ?` until it lifts (other feeds are unaffected).
- Filter specs: `docs/superpowers/specs/`.

## Scheduling (optional)

`scheduler/` has launchd / cron / Windows templates (replace `/path/to/job-pipeline`).
Most people just run `jobhunt` when they want fresh jobs.
