# JobScraper Runbook (CDP + Single Cycle)

## If CDP is busy or timing out

Symptoms:
- Dashboard rows show `CDP connect timeout/busy`.
- Playwright errors like `connect_over_cdp` timeout or `ECONNREFUSED`.

Checks:
1. **Is Chrome running with CDP enabled?**
   - Windows PowerShell (adjust chrome.exe path as needed):
     ```powershell
     $Chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
     if (!(Test-Path $Chrome)) { $Chrome = "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe" }
     $UserData = "$env:LOCALAPPDATA\JobScraperChrome"
     Start-Process $Chrome -ArgumentList @(
       "--remote-debugging-port=9224",
       "--user-data-dir=$UserData",
       "https://www.tanitjobs.com/jobs/",
       "https://www.emploi.nat.tn/fo/Fr/global.php?page=146&=true&FormLinks_Sorting=7&FormLinks_Sorted=7"
     )
     ```
2. **Verify CDP from WSL/Linux:**
   ```bash
   curl http://172.25.192.1:9224/json/version
   ```
   - If this fails, Chrome is not reachable or the port proxy is missing.
3. **If you recently closed Chrome**, restart with the CDP flags above.
4. **If multiple jobs are running**, wait for one cycle to finish (CDP calls are serialized).

## Run a single cycle test

Run a single source the same way the dashboard does:
```bash
cd /home/wassim/clawd/job-scraper
python -m jobscraper.run --source tanitjobs --once --sheet-id <SHEET_ID> --sheet-tab Jobs_Today
python -m jobscraper.run --source aneti --once --sheet-id <SHEET_ID> --sheet-tab Jobs_Today
python -m jobscraper.run --source linkedin --once --linkedin-url "<LINKEDIN_SEARCH_URL>" --sheet-id <SHEET_ID> --sheet-tab Jobs_Today
```

Run the full dashboard loop once (with live table):
```bash
python -m jobscraper.cli dashboard --sheet-id <SHEET_ID> --jobs-today-tab Jobs_Today --all-jobs-tab "All jobs" --interval-min 20
```

Tip: if LinkedIn is blocked, confirm you are logged in to LinkedIn in the CDP Chrome profile.
