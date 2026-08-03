# One-command setup. Run from the repo root:
#
#     powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Creates the venv, installs everything (CUDA wheels only when an NVIDIA GPU is
# actually present), pulls the cleanup model if Ollama is installed, puts a
# shortcut on the Desktop and runs doctor so a broken machine says so here
# rather than at the first dictation.
#
# Written for someone who has never opened a terminal on purpose: every step
# prints what it is doing, and a failure stops the script instead of leaving a
# half-installed tree that fails mysteriously later.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n>>> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

# ── Python ───────────────────────────────────────────────────────────────
Step "Checking Python"
$python = $null
foreach ($candidate in @("py -3.12", "py -3.11", "py -3.10", "python")) {
    $parts = $candidate.Split(" ")
    $exe = $parts[0]
    # NOT $args: that is an automatic variable holding the caller's arguments,
    # and shadowing it at script scope is a trap for the next reader.
    $pre = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
    try {
        $version = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.10") {
            $python = $candidate
            Ok "found Python $version ($candidate)"
            break
        }
    } catch { }
}
if (-not $python) {
    Write-Host @"

Python 3.10 or newer is required and was not found.

Install it from https://www.python.org/downloads/ — tick "Add python.exe to
PATH" in the installer — then run this script again.
"@ -ForegroundColor Red
    exit 1
}

# ── virtual environment ──────────────────────────────────────────────────
Step "Creating the virtual environment (.venv)"
if (Test-Path ".venv") {
    Ok ".venv already exists, reusing it"
} else {
    $parts = $python.Split(" ")
    $exe = $parts[0]
    $pre = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
    & $exe @pre -m venv .venv
    Ok "created"
}
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

Step "Installing dependencies (a few minutes, ~500 MB)"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt --quiet
Ok "done"

# ── CUDA, only if there is a GPU to use it ───────────────────────────────
Step "Looking for an NVIDIA GPU"
$hasNvidia = $false
try {
    $null = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
    $hasNvidia = ($LASTEXITCODE -eq 0)
} catch { }

if ($hasNvidia) {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) -join ", "
    Ok "found: $gpu"
    Step "Installing CUDA runtime libraries (~1 GB — this is the slow part)"
    & $venvPython -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 --quiet
    Ok "done — dictation will run about 10x faster than on the CPU"
} else {
    Warn "no NVIDIA GPU — FortuneVoice will run on the CPU."
    Warn "It works, but expect a few seconds per dictation instead of under one."
}

# ── optional cleanup model ───────────────────────────────────────────────
Step "Checking Ollama (optional — it tidies filler words out of the text)"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    Ok "installed — pulling qwen2.5:1.5b (~1 GB)"
    & ollama pull qwen2.5:1.5b
    Ok "done"
} else {
    Warn "not installed. Dictation works fine without it — you just get the raw"
    Warn "transcript. To add it later: https://ollama.com/download, then"
    Warn "  ollama pull qwen2.5:1.5b"
}

# ── shortcut ─────────────────────────────────────────────────────────────
Step "Putting a shortcut on the Desktop"
& $venvPython scripts\install_shortcut.py
Ok "done"

# ── prove it works ───────────────────────────────────────────────────────
Step "Checking this machine can actually run it"
Write-Host "    (the Whisper model downloads now — about 1.6 GB, once)`n"
& $venvPython -m fortunevoice doctor

Write-Host @"

Setup finished.

Start it from the Desktop shortcut, then hold Ctrl+Alt+Space, speak, and let
go. The text is typed wherever your cursor is.

The icon lives in the notification area, next to the clock — right-click it
for History, Settings and the rest.
"@ -ForegroundColor Green
