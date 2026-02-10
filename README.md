# job-scraper

Job scraper + workflow helper.

What it does:
- Scrapes multiple job boards into a local SQLite DB (`data/jobs.sqlite3`).
- Exports the full DB to `data/all_jobs.csv` and syncs it into the Google Sheet tab **All jobs** (for analytics).
- Appends **relevant** jobs into a lightweight daily inbox tab **Jobs_Today**.
- Scores newly seen relevant jobs with a local LLM (Ollama Qwen) and writes back to `Jobs_Today`.
- You review Jobs_Today, then run a command that transfers the rows into **Jobs** (your editable workflow tab with dropdown + notes).
- Sends **one Pushover notification** per full cycle when new relevant jobs were found.

## Setup

```bash
cd job-scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m playwright install chromium
```

### Config

Create `data/config.env` (copy from `data/config.env.example`):

```bash
cp data/config.env.example data/config.env
```

Fill:
- `SHEET_ID`
- `SHEET_ACCOUNT`
- `CDP_URL` (Chrome/Edge launched with `--remote-debugging-port=9223`)

LinkedIn scoring needs an authenticated Chrome session over CDP (logged in to LinkedIn). If CDP is not reachable, LinkedIn jobs are skipped with a warning.

### Pushover

Create `data/pushover.env`:

```bash
mkdir -p data
cat > data/pushover.env <<'EOF'
PUSHOVER_USER_KEY=...
PUSHOVER_APP_TOKEN=...
EOF
```

### Google Sheet tabs

Create these tabs:
- `Jobs_Today` (scraper output, append-only)
- `Jobs` (your workflow tab: decision dropdown + notes)
- `All jobs` (full export for analytics)

LLM scoring adds columns J:M on Jobs/Jobs_Today:
- llm_score, llm_decision, llm_reasons, llm_model

Recommended `Jobs` schema (A:M):
- source, labels, title, company, location, date_added, url, decision, notes, llm_score, llm_decision, llm_reasons, llm_model

Set a **dropdown** on `Jobs!H:H` with values:
- NEW
- SAVED
- APPLIED
- SKIPPED_NOT_A_FIT
- REJECTED
- ARCHIVED

(We do not use Apps Script. The view/tab limitations are avoided by the Jobs_Today transfer flow.)

## Commands

### Smoke test

Checks: SQLite, CDP, Pushover config, Sheets access.

```bash
python -m jobscraper smoke
```

### Dashboard (full cycle loop)

Runs all sources every `INTERVAL_MIN` minutes.
- Appends new relevant rows into `Jobs_Today`
- Syncs full DB into `All jobs`
- Sends 1 Pushover notification if anything new relevant was found

```bash
python -m jobscraper dashboard
```

Skip LLM scoring (for smoke tests):

```bash
DISABLE_LLM_SCORE=1 python -m jobscraper dashboard
```

### Transfer today inbox into workflow

```bash
python -m jobscraper transfer-today
```

### Score recent relevant jobs (LLM)

```bash
python -m jobscraper score-today --since-hours 6
```

Optional env vars:
- `LLM_MODEL` (default: qwen2.5:7b-instruct)

## Data
- SQLite DB: `data/jobs.sqlite3`
- Full export CSV: `data/all_jobs.csv`
- Run log: `data/run_log.csv`
