# One pass of memory and disk relief for a 16 GB laptop running many Claude
# Code sessions, Docker and FortuneVoice. Needs an administrator PowerShell
# for memory compression and the ClaudeTune task. Never restarts anything
# but the SysMain service, which Enable-MMAgent restarts by itself.
#
#   1. Memory compression and page combining on: pages of idle sessions are
#      compressed in RAM instead of being written to the SSD.
#   2. session_report.ps1: every session with what it runs, and the
#      throwaway leftovers of closed sessions stopped.
#   3. ClaudeTune re-installed from this folder: every 15 minutes it now
#      stops such leftovers too, and no longer trims working sets.
#   4. FortuneVoice's large-v3-turbo model removed, only when config.json
#      picks another model explicitly (turbo is FortuneVoice's default).
#   5. Claude Code keeps session history for 14 days (cleanupPeriodDays),
#      with settings.json backed up first.
#
#   powershell -ExecutionPolicy Bypass -File katana_tune.ps1
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $PSCommandPath

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "NOT ELEVATED - run from an administrator PowerShell." -ForegroundColor Red
    return
}
foreach ($f in 'session_report.ps1', 'autotune.ps1') {
    if (-not (Test-Path (Join-Path $here $f))) {
        Write-Host "missing $f next to this script - download it first" -ForegroundColor Red
        return
    }
}

function Get-State {
    $os = Get-CimInstance Win32_OperatingSystem
    [PSCustomObject]@{
        AvailMB  = $os.FreePhysicalMemory / 1KB
        CommitGB = ($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB
        FreeCGB  = (Get-PSDrive C).Free / 1GB
    }
}
$before = Get-State

# ------------------------------------------------------------------ 1
Write-Host "== 1. memory compression" -ForegroundColor Cyan
$mm = Get-MMAgent
if (-not $mm) {
    Write-Host "  Get-MMAgent returned nothing - is the SysMain service disabled?" -ForegroundColor Red
} elseif ($mm.MemoryCompression -and $mm.PageCombining) {
    Write-Host "  already on" -ForegroundColor DarkGray
} else {
    # Enable-MMAgent restarts SysMain and can report error 352 while still
    # setting the flag, so the result is read back instead of trusted.
    if (-not $mm.MemoryCompression) { Enable-MMAgent -MemoryCompression -ErrorAction SilentlyContinue }
    if (-not $mm.PageCombining) { Enable-MMAgent -PageCombining -ErrorAction SilentlyContinue }
    $mm = Get-MMAgent
    $live = [bool](Get-Process -Name 'Memory Compression' -ErrorAction SilentlyContinue)
    Write-Host ("  MemoryCompression {0}, PageCombining {1}; compression {2}" -f $mm.MemoryCompression,
        $mm.PageCombining, $(if ($live) { 'is working now' } else { 'starts working after the next restart' })) -ForegroundColor Green
}

# ------------------------------------------------------------------ 2
Write-Host ""
Write-Host "== 2. sessions and leftovers" -ForegroundColor Cyan
& (Join-Path $here 'session_report.ps1') -StopLeftovers

# ------------------------------------------------------------------ 3
Write-Host ""
Write-Host "== 3. ClaudeTune" -ForegroundColor Cyan
& (Join-Path $here 'autotune.ps1') -Install

# ------------------------------------------------------------------ 4
Write-Host ""
Write-Host "== 4. unused Whisper model" -ForegroundColor Cyan
$turbo = Join-Path $env:APPDATA 'FortuneVoice\models\models--mobiuslabsgmbh--faster-whisper-large-v3-turbo'
$cfg = $null
try { $cfg = Get-Content (Join-Path $env:APPDATA 'FortuneVoice\config.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
$model = "$($cfg.FVModel)"
$fallback = "$($cfg.FVFallbackModel)"
if (-not (Test-Path $turbo)) {
    Write-Host "  already gone" -ForegroundColor DarkGray
} elseif (-not $model -or $model -match 'turbo' -or $fallback -match 'turbo') {
    # With no FVModel in config.json FortuneVoice loads its default, turbo.
    Write-Host ("  kept: FortuneVoice may load it (FVModel '{0}', FVFallbackModel '{1}')" -f $model, $fallback) -ForegroundColor Yellow
} else {
    $gb = (Get-ChildItem $turbo -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB
    Remove-Item $turbo -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $turbo) {
        Write-Host "  could not remove all of $turbo" -ForegroundColor Red
    } else {
        Write-Host ("  large-v3-turbo removed, {0:N2} GB; FortuneVoice uses {1}" -f $gb, $model) -ForegroundColor Green
    }
}

# ------------------------------------------------------------------ 5
Write-Host ""
Write-Host "== 5. Claude Code history" -ForegroundColor Cyan
$settings = Join-Path $env:USERPROFILE '.claude\settings.json'
try {
    $j = $null
    if (Test-Path $settings) { $j = Get-Content $settings -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop }
    if (-not $j) { $j = New-Object PSObject }
    if ($j.cleanupPeriodDays -eq 14) {
        Write-Host "  already 14 days" -ForegroundColor DarkGray
    } else {
        if (Test-Path $settings) {
            Copy-Item $settings ("{0}.bak-{1}" -f $settings, (Get-Date -Format 'yyyyMMdd-HHmmss')) -ErrorAction Stop
        }
        $j | Add-Member -NotePropertyName cleanupPeriodDays -NotePropertyValue 14 -Force
        # No BOM: Claude Code reads the file as plain UTF-8 JSON.
        [IO.File]::WriteAllText($settings, ($j | ConvertTo-Json -Depth 32), (New-Object Text.UTF8Encoding $false))
        Write-Host "  cleanupPeriodDays 14: older sessions are deleted when Claude Code starts" -ForegroundColor Green
    }
} catch {
    Write-Host "  settings.json left as it was: $($_.Exception.Message)" -ForegroundColor Red
}

# ------------------------------------------------------------- result
$after = Get-State
Write-Host ""
Write-Host "================ result ================" -ForegroundColor Cyan
"available RAM : {0:N0} -> {1:N0} MB" -f $before.AvailMB, $after.AvailMB
"committed     : {0:N1} -> {1:N1} GB" -f $before.CommitGB, $after.CommitGB
"C: free       : {0:N1} -> {1:N1} GB" -f $before.FreeCGB, $after.FreeCGB
Write-Host "Nothing was restarted. The page file shrinks back at the next restart, whenever you choose." -ForegroundColor Yellow
