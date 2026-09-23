# HP-laptop extras on top of system_trim.ps1: memory compression, HP's own
# telemetry services, and the Windows "Soft Landing" tips task.
#
#   powershell -ExecutionPolicy Bypass -File hp_extras.ps1           apply
#   powershell -ExecutionPolicy Bypass -File hp_extras.ps1 -Revert   undo
#
# Everything changed is recorded first, so -Revert puts back exactly what was
# there: the laptop belongs to someone else and goes back to them. Nothing
# here touches Defender, drivers, CryptoPro or the owner's data.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

[CmdletBinding()]
param([switch]$Revert)

$ErrorActionPreference = 'Continue'
$StateFile = Join-Path (Split-Path -Parent $PSCommandPath) 'hp_extras.state.json'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "NOT ELEVATED - run from an administrator PowerShell." -ForegroundColor Red
    return
}

# The two HP processes seen in pc_report: SysInfoCap.exe (HP System Info)
# and TouchpointAnalyticsClientService.exe (HP Insights / Touchpoint
# Analytics). Matched by executable, so hotkey, audio and driver services
# cannot be caught by a loose "HP" name match.
$HpTelemetry = 'SysInfoCap\.exe|TouchpointAnalyticsClientService\.exe'

# ------------------------------------------------------------------ revert
if ($Revert) {
    if (-not (Test-Path $StateFile)) {
        Write-Host "no state file at $StateFile - nothing to revert" -ForegroundColor Yellow
        return
    }
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    foreach ($s in @($state.services)) {
        if (-not $s) { continue }
        Set-Service -Name $s.Name -StartupType $s.StartType -ErrorAction SilentlyContinue
        Start-Service -Name $s.Name -ErrorAction SilentlyContinue
        Write-Host ("  {0,-34} -> {1}" -f $s.Name, $s.StartType) -ForegroundColor Green
    }
    foreach ($t in @($state.tasks)) {
        if (-not $t) { continue }
        Enable-ScheduledTask -TaskPath $t.Path -TaskName $t.Name -ErrorAction SilentlyContinue | Out-Null
        Write-Host "  task $($t.Name) back on" -ForegroundColor Green
    }
    if ($state.mmagent) {
        if (-not $state.mmagent.MemoryCompression) { Disable-MMAgent -MemoryCompression -ErrorAction SilentlyContinue }
        if (-not $state.mmagent.PageCombining) { Disable-MMAgent -PageCombining -ErrorAction SilentlyContinue }
    }
    Write-Host "reverted" -ForegroundColor Cyan
    return
}

# ------------------------------------------------------------------- apply
$state = @{ services = @(); tasks = @(); mmagent = $null }

Write-Host "== memory compression" -ForegroundColor Cyan
$mm = Get-MMAgent
$state.mmagent = @{ MemoryCompression = [bool]$mm.MemoryCompression; PageCombining = [bool]$mm.PageCombining }
# Enable-MMAgent can report "restart failed" (error 352) while still setting
# the flag, so read the result back instead of trusting the call.
if (-not $mm.MemoryCompression) { Enable-MMAgent -MemoryCompression -ErrorAction SilentlyContinue }
if (-not $mm.PageCombining) { Enable-MMAgent -PageCombining -ErrorAction SilentlyContinue }
$mm = Get-MMAgent
Write-Host ("  MemoryCompression {0}, PageCombining {1}" -f $mm.MemoryCompression, $mm.PageCombining) -ForegroundColor Green
if (-not $state.mmagent.MemoryCompression -and $mm.MemoryCompression) {
    Write-Host "  was off: fully effective after the next reboot" -ForegroundColor Yellow
}

Write-Host "== HP telemetry services" -ForegroundColor Cyan
$found = @(Get-CimInstance Win32_Service | Where-Object { $_.PathName -match $HpTelemetry })
if (-not $found) { Write-Host "  none registered as services" -ForegroundColor DarkGray }
foreach ($s in $found) {
    if ($s.StartMode -eq 'Disabled') {
        Write-Host ("  {0,-34} already off" -f $s.Name) -ForegroundColor DarkGray
        continue
    }
    $orig = if ($s.StartMode -eq 'Auto') { 'Automatic' } else { 'Manual' }
    $state.services += @{ Name = $s.Name; StartType = $orig }
    Set-Service -Name $s.Name -StartupType Disabled
    Stop-Service -Name $s.Name -Force -ErrorAction SilentlyContinue
    Write-Host ("  {0,-34} off   ({1})" -f $s.Name, $s.DisplayName) -ForegroundColor Green
}
Get-Process SysInfoCap, TouchpointAnalyticsClientService -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "== Soft Landing tips task" -ForegroundColor Cyan
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskPath -like '\SoftLanding\*' -and $_.State -ne 'Disabled' })
if (-not $tasks) { Write-Host "  nothing to do" -ForegroundColor DarkGray }
foreach ($t in $tasks) {
    $state.tasks += @{ Path = $t.TaskPath; Name = $t.TaskName }
    Disable-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName | Out-Null
    Write-Host "  $($t.TaskName) off" -ForegroundColor Green
}

$state | ConvertTo-Json -Depth 4 | Set-Content -Path $StateFile -Encoding UTF8
Write-Host ""
Write-Host "state saved to $StateFile  (undo with -Revert)" -ForegroundColor Cyan
