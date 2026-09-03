# Run the app natively on Windows — no Docker, no WSL.
# Useful for testing the GUI and the Plex folder tree while Docker Desktop is
# being uncooperative. The Docker path is still what ships to Unraid.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\run-local.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\run-local.ps1 -Port 9000
#
# Requires: Python 3.10+ and ffmpeg on PATH.

param(
    [int]$Port = 8080,
    [int]$MaxHeight = 360,
    [string]$SeasonMode = "single"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path

# --- prerequisites --------------------------------------------------------
$python = $null
foreach ($cmd in @("python", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { $python = $cmd; break }
}
if (-not $python) {
    Write-Host "Python not found on PATH." -ForegroundColor Red
    Write-Host "  winget install Python.Python.3.12"
    exit 1
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ffmpeg not found on PATH." -ForegroundColor Red
    Write-Host "  yt-dlp cannot merge video+audio without it."
    Write-Host "  winget install Gyan.FFmpeg"
    Write-Host "  (then open a NEW terminal so PATH refreshes)"
    exit 1
}

# --- virtualenv -----------------------------------------------------------
if (-not (Test-Path ".venv")) {
    Write-Host "-> creating .venv"
    & $python -m venv .venv
}
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "-> installing dependencies"
& $venvPython -m pip install -q --upgrade pip
& $venvPython -m pip install -q -r requirements.txt

# --- local test dirs ------------------------------------------------------
# These mirror the container's /downloads and /config, so the folder tree you
# get here is exactly the tree Plex will see on Unraid.
$env:OUTPUT_DIR = Join-Path $Root "data\downloads"
$env:CONFIG_DIR = Join-Path $Root "data\config"
New-Item -ItemType Directory -Force -Path $env:OUTPUT_DIR, $env:CONFIG_DIR | Out-Null

$env:MAX_HEIGHT  = "$MaxHeight"      # keeps test downloads fast; 0 = best available
$env:SEASON_MODE = $SeasonMode       # keep this matching your Unraid .env
$env:CONCURRENCY = "2"
$env:PORT        = "$Port"

# --- vendor htmx so the page works offline (harmless if it fails) ---------
$htmx = "app\static\htmx.min.js"
if (-not (Test-Path $htmx) -or (Get-Item $htmx).Length -lt 1000) {
    try {
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js" `
            -OutFile $htmx
    } catch {
        Write-Host "  (couldn't vendor htmx - the page falls back to a CDN)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "-----------------------------------------------"
Write-Host "  library : $($env:OUTPUT_DIR)"
Write-Host "  config  : $($env:CONFIG_DIR)"
Write-Host "  quality : max $($env:MAX_HEIGHT)p (test setting)"
Write-Host "  open    : http://127.0.0.1:$Port"
Write-Host "-----------------------------------------------"
Write-Host ""

& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
