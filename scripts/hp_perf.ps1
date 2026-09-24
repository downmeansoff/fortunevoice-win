# Background-CPU trims for a 2-core laptop, on top of system_trim.ps1 and
# hp_extras.ps1. Each one takes work off a CPU that has nothing to spare,
# without turning any protection off:
#
#   Defender scans   capped at 20% CPU on average and run at low priority.
#                    A cap that is already lower stays as it is.
#                    Real-time protection stays on.
#   Update delivery  downloads straight from Microsoft, no uploading
#                    updates to other PCs (Delivery Optimization peering).
#   Widgets          only reported: Windows refused the policy write even
#                    to an administrator on this laptop. The switch in
#                    Settings > Personalization > Taskbar turns it off.
#
#   powershell -ExecutionPolicy Bypass -File hp_perf.ps1           apply
#   powershell -ExecutionPolicy Bypass -File hp_perf.ps1 -Revert   undo
#
# Original values are recorded first, so -Revert restores exactly them.
# Kept separate from hp_extras.ps1 so that re-running either one cannot
# overwrite the other's record of what the machine looked like before.
# Revert this before 99-rollback.ps1: that one puts the Defender scan cap
# back to the Windows default, and this one restores the value it found.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

[CmdletBinding()]
param([switch]$Revert)

$ErrorActionPreference = 'Continue'
$StateFile = Join-Path (Split-Path -Parent $PSCommandPath) 'hp_perf.state.json'
$DoKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization'
$DshKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Dsh'
$ScanCap = 20

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
# other values, so only create the key when it is missing. Failures throw,
# so the caller reports them instead of printing success.
function Set-RegValue($key, $name, $value) {
    if (-not (Test-Path $key)) { New-Item -Path $key -Force -ErrorAction Stop | Out-Null }
    Set-ItemProperty -Path $key -Name $name -Value $value -Type DWord -ErrorAction Stop
}

# An empty policy key does nothing either way; revert leaves none behind.
function Remove-EmptyKey($key) {
    $k = Get-Item $key -ErrorAction SilentlyContinue
    if ($k -and $k.ValueCount -eq 0 -and $k.SubKeyCount -eq 0) {
        Remove-Item $key -ErrorAction SilentlyContinue
    }
}

function Restore-RegValue($key, $name, $value) {
    if ($null -ne $value) {
        Set-RegValue $key $name ([int]$value)
    } elseif ($null -ne (Get-RegValue $key $name)) {
        Remove-ItemProperty -Path $key -Name $name -ErrorAction Stop
    }
    Remove-EmptyKey $key
}

# ------------------------------------------------------------------ revert
if ($Revert) {
    if (-not (Test-Path $StateFile)) {
        Write-Host "no state file at $StateFile - nothing to revert" -ForegroundColor Yellow
        return
    }
    $st = Get-Content $StateFile -Raw | ConvertFrom-Json
    $ok = $true
    try {
        Set-MpPreference -ScanAvgCPULoadFactor ([int]$st.scanLoad) -EnableLowCpuPriority ([bool]$st.lowCpu) -ErrorAction Stop
        Write-Host ("  Defender scan cap {0}%, low priority {1}" -f $st.scanLoad, [bool]$st.lowCpu) -ForegroundColor Green
    } catch {
        Write-Host "  Defender: $($_.Exception.Message)" -ForegroundColor Red
        $ok = $false
    }
    try {
        Restore-RegValue $DoKey 'DODownloadMode' $st.doMode
        Write-Host "  update delivery as it was" -ForegroundColor Green
    } catch {
        Write-Host "  update delivery: $($_.Exception.Message)" -ForegroundColor Red
        $ok = $false
    }
    # Only the first version of this script tried the Widgets policy.
    if ($st.PSObject.Properties.Name -contains 'widgets') {
        try {
            Restore-RegValue $DshKey 'AllowNewsAndInterests' $st.widgets
            Write-Host "  Widgets policy as it was" -ForegroundColor Green
        } catch {
            Write-Host "  Widgets policy: $($_.Exception.Message)" -ForegroundColor Red
            $ok = $false
        }
    }
    if ($ok) {
        Remove-Item $StateFile -Force
        Write-Host "reverted" -ForegroundColor Cyan
    } else {
        Write-Host "some steps failed; state file kept, run -Revert again" -ForegroundColor Yellow
    }
    return
}

# ------------------------------------------------------------------- apply
if (Test-Path $StateFile) {
    # A second run must not record the already-trimmed values as originals.
    Write-Host "already applied (state file exists); run with -Revert first to re-apply" -ForegroundColor Yellow
    return
}

$mp = Get-MpPreference -ErrorAction SilentlyContinue
if (-not $mp) {
    Write-Host "Get-MpPreference returned nothing - is Defender running?" -ForegroundColor Red
    return
}
$st = @{
    scanLoad = [int]$mp.ScanAvgCPULoadFactor
    lowCpu   = [bool]$mp.EnableLowCpuPriority
    doMode   = Get-RegValue $DoKey 'DODownloadMode'
}
$st | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

Write-Host "== Defender scans" -ForegroundColor Cyan
# 0 means no cap at all. Anything already at or below the target stays.
$cap = if ($st.scanLoad -eq 0 -or $st.scanLoad -gt $ScanCap) { $ScanCap } else { $st.scanLoad }
try {
    Set-MpPreference -ScanAvgCPULoadFactor $cap -EnableLowCpuPriority $true -ErrorAction Stop
    $mp = Get-MpPreference
    Write-Host ("  scan CPU cap {0}% (was {1}%), low priority {2}, real-time protection {3}" -f `
        $mp.ScanAvgCPULoadFactor, $st.scanLoad, $mp.EnableLowCpuPriority,
        (Get-MpComputerStatus).RealTimeProtectionEnabled) -ForegroundColor Green
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "== Update delivery" -ForegroundColor Cyan
# 0 = HTTP only: straight from Microsoft, no peer-to-peer in either direction.
try {
    Set-RegValue $DoKey 'DODownloadMode' 0
    Write-Host "  peer-to-peer off, downloads straight from Microsoft" -ForegroundColor Green
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "== Widgets" -ForegroundColor Cyan
# TaskbarDa is the Settings switch: 0 is off, missing or 1 is on.
$da = Get-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' 'TaskbarDa'
if ($da -eq 0) {
    Write-Host "  off" -ForegroundColor Green
} else {
    Write-Host "  on - turn off by hand: Settings > Personalization > Taskbar > Widgets" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "state saved to $StateFile  (undo with -Revert)" -ForegroundColor Cyan
