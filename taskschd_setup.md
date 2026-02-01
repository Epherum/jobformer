# Windows Task Scheduler setup (every 15 minutes)

## One-time: make sure the repo is on Windows
Suggested path:
- `C:\Users\wassi\clawd\job-scraper`

(Or any folder you like. Just update paths below.)

## One-time: initial Cloudflare pass (interactive)
Open PowerShell:

```powershell
cd C:\Users\wassi\clawd\job-scraper
powershell -ExecutionPolicy Bypass -File .\run_windows.ps1
```

It will ask you to close Edge first (so the profile isn’t locked). Then it launches Edge using your real profile.
If Cloudflare appears, complete it. Once you see results, return to the terminal and press Enter.

## Create the 15-minute scheduled task
Run PowerShell **as your user** (no admin needed):

```powershell
$Repo = "C:\Users\wassi\clawd\job-scraper"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Repo\run_15m_windows.ps1`""
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "job-scraper-tanitjobs" -Action $Action -Trigger $Trigger -Description "Scrape Tanitjobs every 15 minutes" -Force
```

## Logs
- Script output: run manually first to see output.
- DB: `data\jobs.sqlite3`
- Debug HTML: `debug\tanitjobs_last.html`
