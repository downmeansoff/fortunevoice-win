# One pass of memory relief for a 16 GB laptop running many Claude Code
# sessions, Docker and FortuneVoice. Needs an administrator PowerShell for
# memory compression and the ClaudeTune task. Never restarts anything but
# the SysMain service, which Enable-MMAgent restarts by itself.
#
#   1. Memory compression and page combining on: pages of idle sessions are
#      compressed in RAM instead of being written to the SSD.
#   2. session_report.ps1: every session with what it runs, and the
#      throwaway leftovers of closed sessions stopped.
#   3. ClaudeTune re-installed from this folder: every 15 minutes it now
#      stops such leftovers too, and no longer trims working sets.
#
# Disk space is left alone: with plenty free there is nothing to gain from
# deleting models or session history.
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

# ------------------------------------------------------------- result
$after = Get-State
Write-Host ""
Write-Host "================ result ================" -ForegroundColor Cyan
"available RAM : {0:N0} -> {1:N0} MB" -f $before.AvailMB, $after.AvailMB
"committed     : {0:N1} -> {1:N1} GB" -f $before.CommitGB, $after.CommitGB
Write-Host "Nothing was restarted." -ForegroundColor Yellow
