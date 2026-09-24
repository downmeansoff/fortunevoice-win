# Background-CPU trims for a 2-core laptop, on top of system_trim.ps1 and
# hp_extras.ps1. Each one takes work off a CPU that has nothing to spare,
# without turning any protection off:
#
#   Defender scans   capped at 20% CPU on average and run at low priority.
#                    Real-time protection stays on.
#   Update delivery  downloads straight from Microsoft, no uploading
#                    updates to other PCs (Delivery Optimization peering).
#   Widgets          the news board and its WebView2 processes, which
#                    refresh in the background whether or not it is open.
#
#   powershell -ExecutionPolicy Bypass -File hp_perf.ps1           apply
#   powershell -ExecutionPolicy Bypass -File hp_perf.ps1 -Revert   undo
#
# Original values are recorded first, so -Revert restores exactly them.
# Kept separate from hp_extras.ps1 so that re-running either one cannot
# overwrite the other's record of what the machine looked like before.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

[CmdletBinding()]
param([switch]$Revert)

$ErrorActionPreference = 'Continue'
$StateFile = Join-Path (Split-Path -Parent $PSCommandPath) 'hp_perf.state.json'
$DoKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization'
$DshKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Dsh'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "NOT ELEVATED - run from an administrator PowerShell." -ForegroundColor Red
    return
}

function Get-RegValue($key, $name) {
    (Get-ItemProperty $key -Name $name -ErrorAction SilentlyContinue).$name
}

# New-Item -Force on an existing registry key recreates it and drops its
# other values, so only create the key when it is missing.
function Set-RegValue($key, $name, $value) {
    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
    Set-ItemProperty -Path $key -Name $name -Value $value -Type DWord
}

function Restore-RegValue($key, $name, $value) {
    if ($null -eq $value) {
        Remove-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue
    } else {
        Set-RegValue $key $name ([int]$value)
    }
}

# ------------------------------------------------------------------ revert
if ($Revert) {
    if (-not (Test-Path $StateFile)) {
        Write-Host "no state file at $StateFile - nothing to revert" -ForegroundColor Yellow
        return
    }
    $st = Get-Content $StateFile -Raw | ConvertFrom-Json
    Set-MpPreference -ScanAvgCPULoadFactor ([int]$st.scanLoad) -EnableLowCpuPriority ([bool]$st.lowCpu)
    Restore-RegValue $DoKey 'DODownloadMode' $st.doMode
    Restore-RegValue $DshKey 'AllowNewsAndInterests' $st.widgets
    Remove-Item $StateFile -Force
    Write-Host "reverted; sign out and back in for Widgets" -ForegroundColor Cyan
    return
}

# ------------------------------------------------------------------- apply
if (Test-Path $StateFile) {
    # A second run must not record the already-trimmed values as originals.
    Write-Host "already applied (state file exists); run with -Revert first to re-apply" -ForegroundColor Yellow
    return
}

$mp = Get-MpPreference
$st = @{
    scanLoad = [int]$mp.ScanAvgCPULoadFactor
    lowCpu   = [bool]$mp.EnableLowCpuPriority
    doMode   = Get-RegValue $DoKey 'DODownloadMode'
    widgets  = Get-RegValue $DshKey 'AllowNewsAndInterests'
}
$st | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

Write-Host "== Defender scans" -ForegroundColor Cyan
Set-MpPreference -ScanAvgCPULoadFactor 20 -EnableLowCpuPriority $true
$mp = Get-MpPreference
Write-Host ("  scan CPU cap {0}% (was {1}%), low priority {2}, real-time protection {3}" -f `
    $mp.ScanAvgCPULoadFactor, $st.scanLoad, $mp.EnableLowCpuPriority,
    (Get-MpComputerStatus).RealTimeProtectionEnabled) -ForegroundColor Green

Write-Host "== Update delivery" -ForegroundColor Cyan
# 0 = HTTP only: straight from Microsoft, no peer-to-peer in either direction.
Set-RegValue $DoKey 'DODownloadMode' 0
Write-Host "  peer-to-peer off, downloads straight from Microsoft" -ForegroundColor Green

Write-Host "== Widgets" -ForegroundColor Cyan
Set-RegValue $DshKey 'AllowNewsAndInterests' 0
Get-Process Widgets, WidgetService -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "  off; the taskbar button disappears after signing out" -ForegroundColor Green

Write-Host ""
Write-Host "state saved to $StateFile  (undo with -Revert)" -ForegroundColor Cyan
