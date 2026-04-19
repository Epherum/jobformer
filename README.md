# jobformer

Job scraping workflow for Wassim.

It scrapes multiple sources into SQLite, routes relevant jobs into Google Sheets, scores them with a local **llama.cpp** model, and sends notifications.

Current production flow:
- **Keejob**: scrape + structured detail extraction
- **Tanitjobs**: scrape + same-run rich card text capture
- **LinkedIn**: scrape via logged-in Chrome over CDP
- **Scoring**: local **llama.cpp** OpenAI-compatible server on `127.0.0.1:8080`
- **Sheets**:
  - `Sales_Today`
  - `Tech_Today`
  - `Jobs`

## What the app does

Per dashboard cycle:
1. Scrape sources
2. Route relevant jobs into `Sales_Today` / `Tech_Today`
3. Put **oversenior** jobs directly into `Jobs`
4. Extract/fill text cache
5. Score jobs with **llama.cpp**
6. Send notifications:
   - hot jobs
   - Tanitjobs new-post notification
   - quiet issues notification

## Important behavior

- **llama.cpp only**. No Ollama.
- Oversenior jobs are **not sent to the LLM**.
- Oversenior jobs get score `0` and are routed straight to `Jobs`.
- Tanitjobs new jobs always get their own separate notification regardless of score.
- Hot job notifications show:
  - `score | source | reason`

## Repo layout

Important files:
- `data/jobs.sqlite3` → main database
- `data/run_log.csv` → dashboard/source run log
- `data/config.env` → local runtime config
- `data/pushover.env` → Pushover credentials
- `data/llama_server.log` → llama.cpp server log when auto-started by dashboard

## Requirements

- Python 3.10+
- Linux / WSL2
- Windows Chrome available for CDP browsing
- `gog` CLI authenticated for Google Sheets
- local **llama.cpp** build with `llama-server`
- model file present locally

## Fresh setup from scratch

This is the part to follow after a PC reset.

---

## 1) Clone the repo

```bash
git clone https://github.com/Epherum/jobformer.git
cd jobformer
```

## 2) Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
python -m playwright install chromium
```

You should now be able to run:

```bash
source .venv/bin/activate
jobformer --help
```

## 3) Make the global terminal launcher work

If you want `jobformer ...` to work from any terminal, create:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/jobformer <<'SH'
#!/usr/bin/env bash
set -e
cd /home/wassim/jobformer
if [ -z "$GOG_KEYRING_PASSWORD" ] && [ -f /home/wassim/.config/gog/keyring_password ]; then
  read -r GOG_KEYRING_PASSWORD < /home/wassim/.config/gog/keyring_password
  export GOG_KEYRING_PASSWORD
fi
export PYTHONPATH="/home/wassim/jobformer/src${PYTHONPATH:+:$PYTHONPATH}"
exec /home/wassim/jobformer/.venv/bin/jobformer "$@"
SH
chmod +x ~/.local/bin/jobformer
```

Make sure `~/.local/bin` is in your PATH.

Test:

```bash
jobformer --help
jobformer start
```

## 4) Configure Google Sheets access

This project uses `gog`.

You need:
- `gog` installed
- the correct Google account authenticated
- the keyring password available non-interactively

Store the password in:

```bash
mkdir -p ~/.config/gog
printf '%s\n' 'YOUR_KEYRING_PASSWORD' > ~/.config/gog/keyring_password
chmod 600 ~/.config/gog/keyring_password
```

Test that Sheets access works later with:

```bash
jobformer smoke
```

## 5) Build or install llama.cpp

Expected server binary:

```bash
/home/wassim/llama.cpp/build/bin/llama-server
```

If needed:

```bash
git clone https://github.com/ggml-org/llama.cpp.git ~/llama.cpp
cd ~/llama.cpp
cmake -B build
cmake --build build -j
```

Expected model path right now:

```bash
/home/wassim/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
```

## 6) Configure `data/config.env`

Use this shape:

```env
SHEET_ID=YOUR_SHEET_ID
SHEET_ACCOUNT=wassimfekih3@gmail.com

JOBS_TAB=Jobs
JOBS_TODAY_TAB=Jobs_Today
ALL_JOBS_TAB=Jobs
APPLIED_JOBS_TAB=Applied Jobs
SALES_TODAY_TAB=Sales_Today
TECH_TODAY_TAB=Tech_Today

CDP_URL=http://172.21.160.1:9330

LINKEDIN_URL=https://www.linkedin.com/jobs/search/?currentJobId=4357425094&f_TPR=r7200&geoId=102134353&sortBy=DD
LINKEDIN_URLS=https://www.linkedin.com/jobs/search/?currentJobId=4357425094&f_TPR=r7200&geoId=102134353&sortBy=DD,https://www.linkedin.com/jobs/search/?geoId=105015875&f_TPR=r7200&sortBy=DD&f_WT=2,https://www.linkedin.com/jobs/search/?geoId=101282230&f_TPR=r7200&sortBy=DD&f_WT=2,https://www.linkedin.com/jobs/search/?location=Middle%20East&f_TPR=r7200&sortBy=DD&region=ME&f_WT=2

INTERVAL_MIN=20
TEXT_FETCH_DELAY_NORMAL_S=10
TEXT_FETCH_DELAY_CF_S=60
TEXT_FETCH_MAX_JOBS=200

LLM_BACKEND=llama_cpp
LLAMA_CPP_URL=http://127.0.0.1:8080
LLM_MODEL=/home/wassim/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
```

Notes:
- `ALL_JOBS_TAB=Jobs` is correct for the current sheet
- the app still has a legacy `JOBS_TODAY_TAB`, but production routing now uses `Sales_Today` and `Tech_Today`

## 7) Set up Pushover

Create:

```bash
mkdir -p data
cat > data/pushover.env <<'EOF'
PUSHOVER_USER_KEY=YOUR_USER_KEY
PUSHOVER_APP_TOKEN=YOUR_APP_TOKEN
EOF
```

Current notification behavior:
- hot jobs: high priority
- Tanitjobs new jobs: separate notification
- issues: separate low-priority `sound=none`

## 8) Start Windows Chrome in CDP mode

Jobformer expects a Chrome instance on Windows with remote debugging enabled.

Typical command on Windows PowerShell:

```powershell
$Chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (!(Test-Path $Chrome)) { $Chrome = "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe" }
$UserData = "$env:LOCALAPPDATA\JobScraperChrome"
Start-Process $Chrome -ArgumentList @(
  "--remote-debugging-port=9330",
  "--user-data-dir=$UserData"
)
```

Then in that Chrome profile:
- log into **LinkedIn**
- open **Tanitjobs** once if needed
- keep that browser open while Jobformer runs

From WSL, this should resolve via:
- `CDP_URL=http://172.21.160.1:9330`

Quick check:

```bash
curl http://172.21.160.1:9330/json/version
```

If the Windows CDP endpoint is configured but unreachable from WSL, `jobformer smoke`
and `jobformer dashboard --once` automatically fall back to a local headless Chrome
CDP instance. The fallback runs quietly with GPU disabled to avoid noisy headless
WebGL warnings.

## 9) Run checks

```bash
jobformer smoke
jobformer doctor
```

If both look good, run one cycle:

```bash
jobformer dashboard --once
```

If that works, run the continuous dashboard:

```bash
jobformer dashboard
```

---

## What to run after every reboot

From scratch after boot:

### A. Start Windows Chrome with CDP
Use the PowerShell snippet above.

### B. Make sure Chrome is logged in
Especially for:
- LinkedIn
- Tanitjobs if needed

### C. Open terminal in WSL
Then run:

```bash
jobformer smoke
```

### D. Start the app
Either:

```bash
jobformer dashboard
```

or use the menu:

```bash
jobformer start
```

Current menu is intentionally small:
1. Dashboard (continuous)
2. Dashboard (once)
3. Transfer Sales_Today + Tech_Today → Jobs
4. Smoke test

## Sheet structure

Expected tabs:
- `Sales_Today`
- `Tech_Today`
- `Jobs`

Expected columns A:K:
- A `source`
- B `labels`
- C `title`
- D `company`
- E `location`
- F `date_added`
- G `url`
- H `decision`
- I `score`
- J `reason`
- K `feedback`

## Source-specific notes

### Keejob
- detail page fetched over HTTP
- structured fields extracted for scoring
- languages are preserved in text when available

### Tanitjobs
- scraped from listing cards via CDP
- rich card text is captured **during the scrape itself**
- no second discovery pass needed to get preview text
- all new Tanitjobs posts notify separately regardless of score

### LinkedIn
- scraped via logged-in Chrome over CDP
- uses search result cards first
- full description text is fetched later from the job detail page for scoring
- this source is the most sensitive to flaky network and CDP/browser state

## Commands

### Dashboard

Run continuously:

```bash
jobformer dashboard
```

Run once:

```bash
jobformer dashboard --once
```

### Start menu

```bash
jobformer start
```

### Transfer today tabs into Jobs

```bash
jobformer transfer-today
```

### Smoke test

```bash
jobformer smoke
```

### Manual scoring/debug commands

```bash
jobformer extract-text --max-jobs 200
jobformer score-cached --max-jobs 200 --concurrency 1
jobformer doctor
```

## Troubleshooting

### `jobformer` not found
Recreate the launcher in `~/.local/bin/jobformer` and ensure `~/.local/bin` is in PATH.

### `gog` asks for password or fails in non-interactive mode
Make sure this file exists:

```bash
~/.config/gog/keyring_password
```

and that the launcher exports it.

### Transfer command says `All jobs!A:J`
Your config is stale. Fix:

```env
ALL_JOBS_TAB=Jobs
```

### CDP not reachable
Make sure Windows Chrome was started with:
- `--remote-debugging-port=9330`

Then test:

```bash
curl http://172.21.160.1:9330/json/version
```

### LinkedIn is flaky
That is expected sometimes.

Current mitigations already in the code:
- longer timeout
- navigation retries
- reuse of existing LinkedIn jobs tab
- extra readiness waits

### llama.cpp scoring fails
The dashboard auto-starts llama.cpp for scoring, but if you run manual score commands yourself, make sure the server is available on:

```bash
http://127.0.0.1:8080
```

Check:

```bash
curl http://127.0.0.1:8080/health
```

## Current workflow summary

- scrape sources
- route relevant jobs to `Sales_Today` / `Tech_Today`
- route oversenior jobs directly to `Jobs`
- cache text
- score with llama.cpp
- notify
- transfer reviewed jobs from today tabs into `Jobs`, with `APPLIED` rows split into `Applied Jobs`

If doing a complete reset, the shortest recovery checklist is:
1. clone repo
2. create venv and install
3. install Playwright Chromium
4. restore `data/config.env`
5. restore `data/pushover.env`
6. restore `~/.config/gog/keyring_password`
7. ensure llama.cpp + model path exist
8. start Windows Chrome in CDP mode
9. run `jobformer smoke`
10. run `jobformer dashboard`
