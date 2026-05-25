param(
    [switch]$Minimized
)

$ErrorActionPreference = 'Stop'

function Assert-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($current)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated PowerShell window. Right-click PowerShell -> Run as administrator."
    }
}

function Get-WslHostIp {
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -match 'WSL' -and $_.IPAddress -notlike '169.254*' } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $ip) { $ip = '172.21.160.1' }
    return $ip
}

function Test-CdpJson {
    param([string]$BaseUrl)
    return Invoke-WebRequest "$BaseUrl/json/version" -UseBasicParsing -TimeoutSec 5
}

Assert-Admin

$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (!(Test-Path $chrome)) { $chrome = "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe" }
if (!(Test-Path $chrome)) { throw "Chrome not found in Program Files or Program Files (x86)." }

$wslIp = Get-WslHostIp
$port = 9330
$userData = "$env:LOCALAPPDATA\JobScraperChrome"
$ruleName = 'JobformerCDP9330'

$chromeArgs = @(
  "--remote-debugging-port=$port",
  "--remote-debugging-address=127.0.0.1",
  "--remote-allow-origins=*",
  "--user-data-dir=$userData",
  "--new-window",
  "--window-size=1200,900",
  "https://www.tanitjobs.com/jobs/"
)

Write-Host "WSL host IP: $wslIp"
Write-Host "Chrome path: $chrome"
Write-Host "Chrome profile: $userData"

if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -DisplayName $ruleName -Enabled True -Action Allow -Direction Inbound -Profile Any | Out-Null
} else {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Any | Out-Null
}

& netsh interface portproxy delete v4tov4 listenaddress=$wslIp listenport=$port | Out-Null
& netsh interface portproxy add v4tov4 listenaddress=$wslIp listenport=$port connectaddress=127.0.0.1 connectport=$port | Out-Null
Restart-Service iphlpsvc

if ($Minimized) {
    Start-Process -FilePath $chrome -WindowStyle Minimized -ArgumentList $chromeArgs | Out-Null
} else {
    Start-Process -FilePath $chrome -ArgumentList $chromeArgs | Out-Null
}

$localOk = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 750
    try {
        $resp = Test-CdpJson -BaseUrl 'http://127.0.0.1:9330'
        if ($resp.StatusCode -eq 200) {
            $localOk = $true
            break
        }
    } catch {}
}

if (-not $localOk) {
    throw "Chrome CDP did not come up on http://127.0.0.1:9330/json/version"
}

$localResp = Test-CdpJson -BaseUrl 'http://127.0.0.1:9330'
$bridgeResp = Test-CdpJson -BaseUrl "http://${wslIp}:9330"

Write-Host ''
Write-Host 'CDP checks passed.' -ForegroundColor Green
Write-Host "Local CDP:  http://127.0.0.1:9330/json/version"
Write-Host "WSL bridge:  http://${wslIp}:9330/json/version"
Write-Host ''
Write-Host 'Run this in WSL before smoke:' -ForegroundColor Cyan
Write-Host "curl http://${wslIp}:9330/json/version"
Write-Host 'jobformer smoke'
