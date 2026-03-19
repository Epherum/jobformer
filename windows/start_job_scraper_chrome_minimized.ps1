$Chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (!(Test-Path $Chrome)) { $Chrome = "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe" }

$UserData = "$env:LOCALAPPDATA\JobScraperChrome"

$args = @(
  "--remote-debugging-port=9330",
  "--remote-debugging-address=0.0.0.0",
  "--remote-allow-origins=*",
  "--user-data-dir=$UserData",
  "--window-position=-32000,-32000",
  "--window-size=800,600",
  "https://www.tanitjobs.com/jobs/"
)

Start-Process -FilePath $Chrome -WindowStyle Minimized -ArgumentList $args
