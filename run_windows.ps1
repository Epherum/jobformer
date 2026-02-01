$ErrorActionPreference = 'Stop'

# Run from repo root
Set-Location -Path $PSScriptRoot

if (-not (Test-Path .\.venv)) {
  py -3 -m venv .venv
}

. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

# Ensure browsers are installed. For Windows + Edge channel, Playwright uses the installed Edge.
# Still install Playwright's driver bits.
python -m playwright install

$TanitUrl = "https://www.tanitjobs.com/jobs/?listing_type%5Bequal%5D=Job&action=search&keywords%5Ball_words%5D=developpeur&GooglePlace%5Blocation%5D%5Bvalue%5D=&GooglePlace%5Blocation%5D%5Bradius%5D=50"

# Use your REAL Edge profile to reuse the session that passes Cloudflare.
# Edge MUST be fully closed first or the profile will be locked.
Write-Host "Close all Microsoft Edge windows first, then press Enter..."
[void][System.Console]::ReadLine()

$EdgeUserData = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"

# First run: headed, so you can solve Cloudflare if needed. When results are visible, return here and press Enter.
python -m jobscraper.run --source tanitjobs --tanitjobs-url $TanitUrl --browser-channel msedge --user-data-dir $EdgeUserData --headed --once
