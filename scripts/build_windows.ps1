param(
  [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Invoke-Native {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
  )
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "$FilePath $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
  }
}

if (-not $Version) {
  $Version = (python -c "import dc_watch; print(dc_watch.__version__)").Trim()
}

Remove-Item -Recurse -Force build, dist, release -ErrorAction SilentlyContinue
Invoke-Native python -m pytest
Invoke-Native python -m PyInstaller packaging/pyinstaller/dc-watch-gui.spec --noconfirm
Invoke-Native python -m PyInstaller packaging/pyinstaller/dc-watch-cli.spec --noconfirm

$Out = Join-Path $Root "dist\DCWatch"
New-Item -ItemType Directory -Force $Out | Out-Null
Copy-Item -Recurse -Force "dist\DCWatchCLI" (Join-Path $Out "CLI")
Copy-Item -Force "config.example.toml" $Out
Copy-Item -Force "README.md" $Out
if (Test-Path "LICENSE") {
  Copy-Item -Force "LICENSE" $Out
}

New-Item -ItemType Directory -Force "release" | Out-Null
$Zip = Join-Path $Root "release\DCWatch-$Version-win64.zip"
if (Test-Path $Zip) {
  Remove-Item -Force $Zip
}
Compress-Archive -Path $Out -DestinationPath $Zip
Write-Host "Release zip created: $Zip"
