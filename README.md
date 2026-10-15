# Job Discovery Pipeline

Two LLM agents (Claude, Gemini) independently sweep ATS job boards every 6 hours
and append fresh (<24h) SWE jobs to the shared tracker. A single dedup gate
(`pipeline/add_job.py`) is the only writer, so the same job can never be
appended twice, even if both agents find it.

```
[cron/launchd] ── run_claude.sh ──> claude ──> discover.py ──> candidates.json ──┐
                                                                                ├─> add_job.py ──> Google Sheet
[cron/launchd] ── run_gemini.sh ──> gemini ──> discover.py ──> candidates.json ──┘         "Pipeline Jobs" tab
```

- **Claude** owns Greenhouse + Ashby + Lever (public JSON APIs).
- **Gemini** owns Workday + SmartRecruiters (tenant-by-tenant).
- **Muse** (me) keeps the LinkedIn lane and applies to everything.

Sheet tabs used: **Pipeline Jobs** (appended jobs, same 9 columns as the
LinkedIn tab) and **Pipeline Runs** (per-run audit: fetched/appended/dupes/errors).

## One-time setup (~15 min)

### 1. Install

```bash
cd job-pipeline
pip install -r requirements.txt
chmod +x run_claude.sh run_gemini.sh
```

### 2. Google Sheets access (service account)

The agents write via a Google service account. You do this once:

1. Go to [Google Cloud Console](https://console.cloud.google.com) > create a project (any name).
2. Enable the **Google Sheets API** (APIs & Services > Library > search "Google Sheets API" > Enable).
3. APIs & Services > Credentials > Create Credentials > **Service account** > create it (any name).
4. Open the service account > Keys > Add Key > **JSON** > download.
5. Save the file as `service-account.json` inside `job-pipeline/`.
6. Copy the service account's email (looks like `xxx@xxx.iam.gserviceaccount.com`).
7. Open the [job tracker sheet](https://docs.google.com/spreadsheets/d/1K4qWzt0ew0Dz4_PVn5iKKn3uf896Mhwzoi9uRqAJVoY/edit)
   > Share > paste the email > **Editor** > Send.

Never commit `service-account.json` anywhere.

### 3. Test (offline first, then live)

```bash
# Offline: fetchers + dedup logic, no sheet writes
python3 pipeline/discover.py --agent test --sources greenhouse,ashby
python3 pipeline/add_job.py --agent test --input runs/candidates_test_*.json --dry-run

# Live: appends to the real sheet (creates "Pipeline Jobs" + "Pipeline Runs" tabs)
python3 pipeline/add_job.py --agent test --input runs/candidates_test_*.json
```

### 4. Schedule it

- **macOS**: edit the two `.plist` files in `scheduler/` (replace `/path/to/job-pipeline`),
  copy to `~/Library/LaunchAgents/`, then
  `launchctl load ~/Library/LaunchAgents/com.jobpipeline.claude.plist` (and gemini).
- **Linux**: `crontab -e`, paste the lines from `scheduler/cron.txt` with your path.
- **Windows**: follow `scheduler/windows.md`.

Then do one manual run of each to confirm end-to-end:
`./run_claude.sh` and `./run_gemini.sh`, and check the "Pipeline Runs" tab.

## How dedup works

1. Each run rebuilds the seen-set from **both** job tabs ("Pipeline Jobs" and
   "LinkedIn Easy Apply"), so the laptop agents and Muse's LinkedIn lane can
   never double-append the same job.
2. URL key: tracking params (`utm_*`, `gclid`, …) stripped, ATS identifiers
   (`gh_jid`, etc.) kept, host lowercased.
3. Content key: normalized `company | role | location` catches the same posting
   mirrored across two boards with different URLs.
4. A local SQLite registry (`pipeline/seen.db`) makes re-runs idempotent and
   keeps an audit trail.

## Monitoring

- **Pipeline Runs** tab: one row per agent run (timestamp, sources, fetched,
  appended, dup-url, dup-content, errors). This is also how Muse watches run health.
- `logs/`: per-run agent transcripts on your machine.

## Troubleshooting

- `service account key not found`: the JSON isn't at `job-pipeline/service-account.json`
  (or `GOOGLE_SERVICE_ACCOUNT` env var).
- `403` from Sheets: the sheet wasn't shared with the service-account email.
- A board returns errors two runs in a row: the agent reports it; prune or fix the token.
- Workday tenants are finicky by design; Gemini's prompt covers per-tenant verification.

## Notes / decisions

- Indeed RSS is dead (returns "Not found"); dropped from v1.
- Glassdoor is anti-bot; not worth a lane.
- Location filter: US states + "remote", but "São Paulo, Remote"-style
  non-US-remote postings are excluded.
- Filters: `config/profile.json` (role, level, YOE, sponsorship, location, stack score, per-company cap). Agents must not edit it. Spec: `docs/superpowers/specs/2026-10-15-job-filters-design.md`.
