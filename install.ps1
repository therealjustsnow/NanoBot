<#
.SYNOPSIS
    NanoBot setup for Windows (PowerShell).

.DESCRIPTION
    Installs missing dependencies via WinGet (Git, Python 3.12, FFmpeg),
    creates a local virtual environment in .\venv, installs Python
    dependencies, and creates config.ini from the example if it doesn't
    exist yet.

    Safe to re-run: every step is idempotent.

.PARAMETER NoSystem
    Skip the WinGet dependency installs (pip/venv/config only).

.EXAMPLE
    .\install.ps1
    .\install.ps1 -NoSystem
#>
param(
    [switch]$NoSystem
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host " !  $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host " !! $msg" -ForegroundColor Red; exit 1 }

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Find-Python {
    # Prefer the Windows Python launcher with an explicit 3.11+ version.
    foreach ($ver in @("3.13", "3.12", "3.11")) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py "-$ver" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", "-$ver") }
        }
    }
    # Fall back to python on PATH, as long as it's 3.11+.
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return @("python") }
    }
    return $null
}

function Install-IfMissing($checkCmd, $wingetId, $label) {
    if (Get-Command $checkCmd -ErrorAction SilentlyContinue) {
        Write-Host "    $label already installed."
        return
    }
    Write-Host "    Installing $label via WinGet..."
    winget install --id $wingetId --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Warn "WinGet install of $label may have failed (exit code $LASTEXITCODE)."
    }
    Refresh-Path
}

# -- 1. System dependencies ---------------------------------------------------
if (-not $NoSystem) {
    Say "Installing system dependencies"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Warn "WinGet not found. Install dependencies manually, then re-run with -NoSystem:"
        Warn "  Git:    https://git-scm.com/download/win"
        Warn "  Python: https://www.python.org/downloads/  (3.11+, check 'Add to PATH')"
        Warn "  FFmpeg: https://ffmpeg.org/download.html   (needed for the Music cog)"
        Die  "Cannot continue without WinGet."
    }
    Install-IfMissing "git"    "Git.Git"            "Git"
    Install-IfMissing "ffmpeg" "Gyan.FFmpeg"        "FFmpeg"
    if (-not (Find-Python)) {
        Install-IfMissing "__no_such_cmd__" "Python.Python.3.12" "Python 3.12"
    } else {
        Write-Host "    Python 3.11+ already installed."
    }
} else {
    Say "Skipping system dependencies (-NoSystem)"
}

# -- 2. Find Python 3.11+ -----------------------------------------------------
Say "Looking for Python 3.11+"
$python = Find-Python
if (-not $python) {
    Die "No Python 3.11+ found. If you just installed it, close and reopen this terminal, then re-run with -NoSystem."
}
$pyVersion = & $python[0] $python[1..($python.Count - 1)] --version
Write-Host "    Using: $($python -join ' ') ($pyVersion)"

# -- 3. Virtual environment ---------------------------------------------------
Say "Setting up virtual environment (.\venv)"
if (-not (Test-Path "venv\Scripts\python.exe")) {
    & $python[0] $python[1..($python.Count - 1)] -m venv venv
    if ($LASTEXITCODE -ne 0) { Die "venv creation failed." }
} else {
    Write-Host "    venv already exists — reusing it."
}
$vpy = ".\venv\Scripts\python.exe"

# -- 4. Python dependencies ---------------------------------------------------
Say "Installing Python dependencies"
& $vpy -m pip install --upgrade pip
& $vpy -m pip install --upgrade -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "pip install failed — see output above." }

# -- 5. Config file -------------------------------------------------------------
Say "Config"
if (Test-Path "config.ini") {
    Write-Host "    config.ini already exists — leaving it untouched."
} else {
    Copy-Item "example_config.ini" "config.ini"
    Write-Host "    Created config.ini from example_config.ini."
}

# -- Done -----------------------------------------------------------------------
Write-Host "`n✅ Setup complete!`n" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Edit config.ini and paste your bot token"
Write-Host "     (https://discord.com/developers/applications -> Bot -> Token)"
Write-Host "  2. Enable Server Members, Message Content and Presence intents"
Write-Host "     in the Developer Portal (same page)."
Write-Host "  3. Start the bot:  run.bat  (or .\venv\Scripts\python.exe run.py)"
