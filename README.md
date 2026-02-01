# job-scraper

Minimal job scraper that runs every 15 minutes and stores new jobs in SQLite.

## Setup

```bash
cd job-scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m playwright install chromium
```

## Tanitjobs
Tanitjobs is protected by Cloudflare. This scraper uses a **persistent Playwright profile** so you can solve the challenge once in a headed browser, then reuse the cookies.

### Recommended: Windows + your real Edge profile
Cloudflare often blocks automated profiles. The most reliable approach is to run Playwright with the installed **Microsoft Edge** and point it at your **real Edge user profile** directory, so it reuses the same verified session.

### 1) First run (headed) to pass Cloudflare

```bash
python -m jobscraper.run --source tanitjobs \
  --tanitjobs-url "<your search url>" \
  --browser-channel msedge \
  --user-data-dir "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data" \
  --headed --once
```

A browser window opens. Complete the Cloudflare check and make sure you can see the job results page. The script waits ~2 minutes before scraping. Cookies are saved under `./state/`.

### 2) Normal run

```bash
python -m jobscraper.run --source tanitjobs \
  --tanitjobs-url "<your search url>" \
  --browser-channel msedge \
  --user-data-dir "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data" \
  --once
```

## Data
- SQLite DB: `./data/jobs.sqlite3`
- Debug HTML snapshots: `./debug/`

## Next
- Add notification (Discord/email)
- Add cron/systemd timer
