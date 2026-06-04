param(
  [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $Version) {
  $Version = (python -c "import dc_watch; print(dc_watch.__version__)").Trim()
}

$Out = Join-Path $Root "dist\DCWatch"
if (-not (Test-Path $Out)) {
  throw "dist\DCWatch does not exist. Run scripts\build_windows.ps1 first."
}

New-Item -ItemType Directory -Force "release" | Out-Null
$Zip = Join-Path $Root "release\DCWatch-$Version-win64.zip"
if (Test-Path $Zip) {
  Remove-Item -Force $Zip
}
Compress-Archive -Path $Out -DestinationPath $Zip
Write-Host "Release zip created: $Zip"
