$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

. .\.venv\Scripts\Activate.ps1

$TanitUrl = "https://www.tanitjobs.com/jobs/?listing_type%5Bequal%5D=Job&action=search&keywords%5Ball_words%5D=developpeur&GooglePlace%5Blocation%5D%5Bvalue%5D=&GooglePlace%5Blocation%5D%5Bradius%5D=50"

$EdgeUserData = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"

python -m jobscraper.run --source tanitjobs --tanitjobs-url $TanitUrl --browser-channel msedge --user-data-dir $EdgeUserData --once
