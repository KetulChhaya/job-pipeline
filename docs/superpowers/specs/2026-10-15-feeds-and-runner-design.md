# GitHub job feeds + terminal runner: design

Date: 2026-10-15
Status: approved in chat, pending written-spec review
Builds on: `2026-10-15-job-filters-design.md` (filters, enrichment, `add_job` gate)

## Goal

1. Add three GitHub-hosted job lists as a source, run deterministically
   (no LLM), through the same filters and dedup gate as the board lanes.
2. One command, `jobhunt`, runs every lane from any folder. It shows compact
   live progress, then a summary of what was appended, the agent reports,
   the log path, and a macOS notification. Nobody has to open the project.

## Success criteria

1. `jobhunt` runs Claude lane → Gemini lane → feeds step, and prints a total
   line ("N jobs added (claude a, gemini b, feeds c)").
2. While a lane runs, the terminal shows a progress line per board/feed,
   a filter line (kept / dropped by reason), and an append line, all
   produced by pipeline code, whatever the agent CLI does.
3. The end-of-lane summary lists every job appended in that lane (score,
   company, role, YOE, link), taken from the `seen` registry rather than
   from the agent's own words.
4. A job listed in two feeds, or in a feed and a board lane, lands in the
   sheet once.
5. Feed jobs with a Greenhouse, Lever, Ashby, Workday or SmartRecruiters
   link get their description fetched, so the YOE and sponsorship checks run.
6. `run_claude.sh` and `run_gemini.sh` still work on their own.

## Non-goals

- Hourly cron, a lock around add_job, registry-before-append (spec 2).
- Board-slug harvesting from these feeds (spec 3).
- Workable / iCIMS description fetching (those links stay `desc ?`).

## Feeds

`config/boards.json` gains:

```json
"github": [
 {"name": "simplify-newgrad", "format": "simplify",
  "url": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"},
 {"name": "speedyapply-newgrad-usa", "format": "speedyapply",
  "url": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md"},
 {"name": "jobright-swe-newgrad", "format": "jobright",
  "url": "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/master/README.md"}
]
```

Order matters: within one run, ties in `merge()` keep the earliest feed,
and jobright rows (no description) score lower anyway.

| Format | Shape (verified 2026-10-15) | Fresh when | Extra |
|---|---|---|---|
| simplify | JSON list: `company_name, title, locations[], url, date_posted` (epoch), `active, is_visible, sponsorship` | `active and is_visible` and posted ≤ window | `sponsorship`: "Offers Sponsorship" → yes; "Does Not Offer Sponsorship" / "U.S. Citizenship is Required" → no; else unknown |
| speedyapply | Markdown table `Company \| Position \| Location \| Salary \| Posting \| Age`; company in `<strong>`, link in `<a href>`, age `Nd` | age days × 24 ≤ window hours | — |
| jobright | Markdown table `Company \| Job Title \| Location \| Work Model \| Date Posted`; `**[Name](url)**`, `**[Title](link)**`, `↳` = same company as row above, date `Oct 14` | posted date ≥ yesterday (day granularity) | link is a jobright.ai page |

Each feed row becomes the normal job dict (`source: github/<name>`), plus
`feed: <name>` and `feed_sponsorship` (simplify only).

### ATS link resolution (`pipeline/atslinks.py`)

`resolve(url) -> (ats, detail_url)`, or `(None, "")` when unknown:

| ATS | Link shape | Detail source |
|---|---|---|
| greenhouse | `(job-)boards.greenhouse.io/<slug>/jobs/<id>`, `embed/job_app?for=<slug>&token=<id>` | `boards-api.greenhouse.io/v1/boards/<slug>/jobs/<id>` → `content` |
| lever | `jobs.lever.co/<slug>/<uuid>` | `api.lever.co/v0/postings/<slug>/<uuid>` → descriptionPlain + lists + additionalPlain, `country` |
| ashby | `jobs.ashbyhq.com/<slug>/<uuid>` | board list `api.ashbyhq.com/posting-api/job-board/<slug>`, fetched once per slug per run, then matched by id |
| workday | `<tenant>.wdN.myworkdayjobs.com/[<locale>/]<board>/job/...` | `.../wday/cxs/<tenant>/<board>/job/...` (existing Workday detail parser) |
| smartrecruiters | `jobs.smartrecruiters.com/<company>/<id>` | existing SmartRecruiters detail parser |
| jobright | `jobright.ai/jobs/info/<id>` | the page's schema.org `JobPosting` JSON-LD → description + `jobLocation` country; `isH1bSponsor: true` becomes `sponsor ✓` when the description is silent |

### Filter changes

- Feed jobs use `level.drop` minus `level.feed_allow` (new profile key:
  new grad, new college grad, college grad, graduate, university, AMTS).
  Intern, Staff, Principal, Lead, Manager etc. still drop, and the YOE
  check (≤ 5) still applies.
- Sponsorship: the description decides first; if it says nothing, the
  feed's `feed_sponsorship` is used (phrase `simplify: <value>`).

### Cross-source dedup

`discover.run_filters` also skips a job when the same `company_key` +
`title_key` was appended (any lane) in the last 3 days. It counts as
`already_known`. This catches one posting reached through different
links (feed → ATS URL, jobright URL, board lane URL).

## Terminal runner

### Progress lines (`pipeline/progress.py`)

`init(agent)` points the module at `logs/progress-<agent>.log` (or
`JOB_PROGRESS_FILE` if set); `progress(msg)` appends one line and never
raises. `discover.py` and `add_job.py` call it:

```
fetching 37 boards (greenhouse 28, ashby 7, lever 2)
[ 5/37] greenhouse/stripe           41 fresh
[ 6/37] ashby/notion                 0 fresh
! greenhouse/acme: HTTPError: 404
filtering 164 jobs (12 already appended)
descriptions fetched: 4
kept 1 · dropped family 79, location 72
appended 1 to Pipeline Jobs (dup link 0, dup content 0)
```

### `pipeline/runner.py`

```
runner.py --agent NAME --label TEXT [--stdin FILE] [--stream claude|lines]
          [--retries N --retry-wait SECONDS] -- COMMAND...
runner.py --agent feeds --label TEXT --direct SOURCES
runner.py --total AGENTS
```

Shared options: `--db PATH` (registry used by discover, add_job and the
summary; default `pipeline/seen.db`) and, with `--direct` only, `--dry-run`
(add_job `--dry-run`: no sheet writes).

- Header: `━━ Claude · Greenhouse · Ashby · Lever · started 10:17 ━━`.
- Clears the agent's progress file, starts the command, and prints new
  progress lines as `  · …` while it runs.
- Agent output:
  - `--stream claude`: Claude CLI `--output-format stream-json`. Text
    blocks give one narration line each (`  › …`, first line, ≤ 110
    chars); `tool_use` gives `  › Bash: <command>`; the `result` event
    gives the report.
  - `--stream lines` (agy): each stdout line is narration; the report is
    everything from the first Markdown heading or table on.
  - Everything is written to `logs/<agent>-<YYYYMMDD-HHMM>.log` as
    readable text.
- `--retries`: on a non-zero exit, prints `  ! attempt 1 failed (exit 1),
  retrying in 120s` (moves today's Gemini 503 loop out of the shell).
- `--direct SOURCES`: runs `discover.py --agent <agent> --sources ...`,
  then `add_job.py` on the printed candidates file. No LLM.
- End of lane:

```
━━ Claude done · 5m 12s · 1 job added ━━
   25  Asana          Software Engineer                       2+    https://…
  Agent report:
    …(first 40 lines; "… N more lines in the log")
  Log:  /Users/…/logs/claude-20261015-1017.log
  View: less +G /Users/…/logs/claude-20261015-1017.log
```

  - Appended rows (top 25 by score, then `… N more`): `seen` rows for this agent with `first_seen` ≥ lane
    start; score / YOE joined from this lane's `runs/candidates_*.json`.
  - Errors shown only when present: non-zero exit, `!` progress lines.
  - Writes `logs/last-run-<agent>.json` (`added`, `jobs`, `exit`, `seconds`).
  - Sends a macOS notification unless `JOB_NOTIFY=0`.
- `--total claude,gemini,feeds`: reads the `last-run` files newer than
  `JOB_RUN_STARTED`, prints `━━ Total: N jobs added (claude a, gemini b,
  feeds c) · 23m ━━` and sends one notification.

### Scripts

- `run_claude.sh`: `runner.py --agent claude --stdin prompts/claude.md
  --stream claude -- claude -p --verbose --output-format stream-json ...`
  (same permission flags as today).
- `run_gemini.sh`: `runner.py --agent gemini --stream lines --retries 3
  --retry-wait 120 -- agy -p "$(cat prompts/gemini.md)" ...` (same flags).
- `run_feeds.sh` (new): `runner.py --agent feeds --direct github`.
- `run_all.sh` (new): sets `JOB_NOTIFY=0` and `JOB_RUN_STARTED`, runs the
  three scripts (a failing lane doesn't stop the next), then `runner.py
  --total`.
- `~/.zshrc`: `alias jobhunt="$HOME/Work/job-pipeline/run_all.sh"` (added once,
  only if absent). Not `jobs`: that is a zsh builtin (lists background tasks).

## Error handling

| Situation | Behavior |
|---|---|
| Feed URL down / malformed | `! github/<name>: …` progress line, other feeds continue |
| Markdown row that doesn't parse | skipped, counted in a `skipped N rows` progress line |
| ATS link unknown or detail fetch fails | kept, `desc ?` (existing behavior) |
| Ashby board list fails | every job of that slug `desc ?`, one request per slug per run |
| Agent CLI not found / exits non-zero | summary shows `! agent exited with code X`, log path still printed |
| Progress file not writable | progress lines are lost, the run is unaffected |
| `osascript` missing | notification skipped silently |

## Testing

- `tests/test_feeds.py`: real-shaped samples of each format, including
  `↳` rows, `Oct 14` dates across a year boundary, inactive or
  invisible Simplify rows, and age filtering.
- `tests/test_atslinks.py`: each link shape → `(ats, detail_url)`, plus
  unknown hosts.
- `tests/test_enrich.py` additions: greenhouse / lever / ashby detail parsing
  (fake HTTP), feed sponsorship fallback.
- `tests/test_title.py` additions: new-grad titles kept for feed jobs,
  dropped for board jobs; Intern still dropped for both.
- `tests/test_discover.py` additions: same company + title within 3 days skipped
  across sources; progress lines written.
- `tests/test_runner.py`: claude stream-json parsing, report extraction,
  summary from a temp `seen.db` + candidates file, retry loop and live
  progress against a fake agent script, notification arguments (no real
  osascript), total line.
- End-to-end: `run_feeds.sh` against a copy of `seen.db` with add_job in
  dry-run mode (`runner.py --agent feeds --direct github --db <copy> --dry-run`), output shown to the user before
  the first real `jobs`.

## Revisions after the live prototype run (2026-10-15 11:07)

- jobright pages carry the full description, a structured country and the
  company's H-1B history, so jobright links are fetched like an ATS. In the
  dry run that raised sponsorship drops from 3 to 33.
- speedyapply's "Other" table has no Salary column: rows have 5 or 6 cells.
- More citizenship/clearance wording: "citizenship is a requirement",
  "requires U.S. citizenship", "obtain ... clearance".
- The end-of-lane table shows the top 25 rows; the rest are in the sheet.
- Alias renamed `jobhunt` (see Scripts).
