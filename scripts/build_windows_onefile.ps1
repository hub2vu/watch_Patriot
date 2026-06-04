param(
  [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $SkipTests) {
  python -m pytest
}

Remove-Item -Recurse -Force build, dist-onefile -ErrorAction SilentlyContinue
python -m PyInstaller packaging/pyinstaller/dc_watch_gui_launcher.py --name DCWatch --windowed --onefile --distpath dist-onefile --add-data "config.example.toml;." --add-data "README.md;."
python -m PyInstaller packaging/pyinstaller/dc_watch_cli_launcher.py --name DCWatchCLI --console --onefile --distpath dist-onefile --add-data "config.example.toml;." --add-data "README.md;."
Write-Host "One-file executables created in dist-onefile"
