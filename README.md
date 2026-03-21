# jobformer

Personal job scraper + lightweight workflow helper.

It is designed to:
- scrape multiple job sources into a local SQLite DB
- append only *relevant* jobs into a daily inbox tab (`Jobs_Today`) in Google Sheets
- score those inbox rows with a local LLM (llama.cpp local model) and write back a score + short reason
- let you transfer the inbox into your main workflow tab (`Jobs`)

## What you get

Local files:
- SQLite DB: `data/jobs.sqlite3`
- Run log: `data/run_log.csv`

Google Sheets tabs:
- `Jobs_Today`: daily inbox (append-only)
- `Jobs`: your workflow tab (editable)
- `All jobs`: optional, full export (manual command)

## Quick start (Linux/WSL)

### 1) Install

This repo supports both a local venv and pipx.

#### Option A: pipx (recommended)

```bash
sudo apt-get update
sudo apt-get install -y pipx
pipx ensurepath

# From a cloned repo
pipx install /path/to/job-scraper
```

You should now have a global `jobformer` command:

```bash
jobformer --help
```

#### Option B: local venv

```bash
cd job-scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2) Playwright browser (required)

We use Playwright for CDP (Chrome remote debugging) navigation/extraction.

```bash
python3 -m playwright install chromium
```

### 3) Config

Create `data/config.env` (copy from example):

```bash
cp data/config.env.example data/config.env
```

Edit `data/config.env`:

Required:
- `SHEET_ID=...`
- `SHEET_ACCOUNT=...` (the Google account that `gog` is authenticated for)
- `CDP_URL=http://172.21.160.1:9330` (WSL -> Windows host CDP endpoint)

Optional:
- `LINKEDIN_URLS=...` (comma-separated)
- `INTERVAL_MIN=20`
- `TEXT_FETCH_MAX_JOBS=200`

### 4) Google auth (gog)

This project uses the `gog` CLI for Google Sheets access.
Make sure the agent user is authenticated for the account in `SHEET_ACCOUNT`.

If Sheets calls fail, run a minimal test:

```bash
jobformer smoke
```

## Windows: start Chrome in CDP mode

The simplest reliable approach is: run a dedicated Chrome profile with remote debugging enabled.

This repo ships two PowerShell helpers in `windows/`:
- `windows/start_job_scraper_chrome.ps1`
- `windows/start_job_scraper_chrome_minimized.ps1`

Run one of them on Windows. It launches Chrome with:
- `--remote-debugging-port=9330`
- a dedicated user-data-dir (`%LOCALAPPDATA%\JobScraperChrome`)

Then set in `data/config.env`:

```env
CDP_URL=http://172.21.160.1:9330
```

Notes:
- Log into LinkedIn/Tanitjobs in that Chrome window.
- Keep that Chrome running while the scraper runs.

## Google Sheet schema

Tabs `Jobs_Today` and `Jobs` should have these columns:

A: source
B: labels
C: title
D: company
E: location
F: date_added
G: url
H: decision
I: score (LLM score)
J: reason (short justification)

`Jobs_Today` is append-only. `Jobs` is your workflow.

Recommended dropdown for `Jobs!H:H` (decision):
- NEW
- SAVED
- APPLIED
- SKIPPED_NOT_A_FIT
- REJECTED
- ARCHIVED

## Main commands

### `jobformer doctor`
Best-effort environment check for day-to-day reliability.

### `jobformer smoke`
Checks: SQLite, CDP connectivity, Pushover config (if enabled), Sheets access.

### `jobformer dashboard`
Runs a full cycle loop every `INTERVAL_MIN` minutes:
- scrape sources
- append relevant rows to `Jobs_Today`
- extract text + score cached rows (incremental progress in the dashboard)
- send a single notification per cycle (if configured)

One-shot:

```bash
jobformer dashboard --once
```

### `jobformer transfer-today`
Moves all rows from `Jobs_Today` into `Jobs`, then clears `Jobs_Today`.

### Scoring and extraction

Extract page text into cache:

```bash
jobformer extract-text --max-jobs 200
```

Score from cached text:

```bash
jobformer score-cached --max-jobs 200 --concurrency 1
```

Score recent jobs (alternative path):

```bash
jobformer score-today --since-hours 6
```

### Manual Cloudflare workaround (Tanitjobs and similar)

Some sites (notably Tanitjobs job detail pages) can trigger Cloudflare challenges.
When that happens, the fastest workflow is:

1) Open the blocked job URLs manually in the CDP Chrome window (so Cloudflare clears).
2) Run:

```bash
jobformer score-open-tabs
```

This command:
- reads the currently open CDP tabs (no navigation)
- extracts text from those already-open pages
- writes cache entries
- scores any matching unscored `Jobs_Today` rows and updates columns I:J

### Full DB export to `All jobs` (manual)

This is intentionally NOT part of the dashboard pipeline.

```bash
jobformer push-all-jobs
```

## Troubleshooting

### CDP not reachable
- Ensure Chrome is running with `--remote-debugging-port=9330`
- From WSL, the Windows host is typically the default gateway (example `172.21.160.1`).
- Confirm `http://172.21.160.1:9330/json/version` is reachable from WSL.

### Tanitjobs redirects change the URL
Tanitjobs can redirect short URLs like `/job/<id>/` to a slug URL.
We canonicalize both forms to the same `/job/<id>` for matching.

### Dependencies
- Python >= 3.10
- Playwright Chromium installed (`python3 -m playwright install chromium`)
- Required: llama.cpp server running for local LLM scoring

---

If you re-clone from scratch: follow “Quick start”, then run `jobformer doctor` and `jobformer dashboard --once`.
