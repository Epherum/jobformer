# Simple CDP workflow (WSL -> Windows) for JobScraper
# Run this script in a normal PowerShell.
# Then run the portproxy/firewall block in an *Admin* PowerShell.

$ErrorActionPreference = "Stop"

$Port = 9224
$ListenAddr = "172.25.192.1"   # Windows host IP from WSL
$ConnectAddr = "127.0.0.1"     # where Chrome binds

$Chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (!(Test-Path $Chrome)) { $Chrome = "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe" }
if (!(Test-Path $Chrome)) { throw "chrome.exe not found" }

$UserData = "$env:LOCALAPPDATA\JobScraperChrome"

Write-Host "1) Close Chrome if it's open." -ForegroundColor Cyan
Write-Host "2) Starting Chrome with CDP on localhost:$Port" -ForegroundColor Cyan

Start-Process -FilePath $Chrome -ArgumentList @(
  "--remote-debugging-port=$Port",
  "--user-data-dir=$UserData",
  "https://www.tanitjobs.com/jobs/"
)

Write-Host "\nNow open an *Admin* PowerShell and run:" -ForegroundColor Yellow
Write-Host "netsh interface portproxy delete v4tov4 listenaddress=$ListenAddr listenport=$Port" -ForegroundColor Yellow
Write-Host "netsh interface portproxy add v4tov4 listenaddress=$ListenAddr listenport=$Port connectaddress=$ConnectAddr connectport=$Port" -ForegroundColor Yellow
Write-Host "New-NetFirewallRule -DisplayName 'JobScraper CDP $Port (WSL)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -ErrorAction SilentlyContinue" -ForegroundColor Yellow

Write-Host "\nWSL test:" -ForegroundColor Green
Write-Host "curl -s http://$ListenAddr:$Port/json/version" -ForegroundColor Green

Write-Host "\nIf that works, set in WSL: CDP_URL=http://$ListenAddr:$Port" -ForegroundColor Green
