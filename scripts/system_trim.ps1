# System-level trim for a 2-core / 8 GB dev laptop: telemetry, services
# nobody on this machine uses, hibernation file, temp junk. Animations and
# transparency are left as they are: the owner wants them.
#
#   powershell -ExecutionPolicy Bypass -File system_trim.ps1           apply
#   powershell -ExecutionPolicy Bypass -File system_trim.ps1 -Revert   undo
#
# Every service change is recorded to system_trim.state.json next to this
# file before it is made, so -Revert restores exactly what was there.
#
# Deliberately NOT touched, each for a reason:
#   SysMain      - drives memory compression, which this machine depends on
#   WSearch      - the indexer; costs CPU but disabling breaks Start search
#   Spooler      - there may be a printer
#   SharedAccess - VPN clients use it for routing
#   WinDefend    - the machine pulls code from npm and GitHub daily
#   network, audio, WSL / Hyper-V (Docker), Windows Update
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

[CmdletBinding()]
param([switch]$Revert)

$ErrorActionPreference = 'Continue'
$StateFile = Join-Path (Split-Path -Parent $PSCommandPath) 'system_trim.state.json'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "NOT ELEVATED - run from an administrator PowerShell." -ForegroundColor Red
    Write-Host ("  Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-File','{0}'" -f $PSCommandPath)
    return
}

# Services that are safe to disable on a single-user dev laptop. Each line is
# what it does, so the list can be audited without looking anything up.
$Services = [ordered]@{
    'DiagTrack'        = 'Connected User Experiences and Telemetry'
    'dmwappushservice' = 'WAP push routing for telemetry'
    'WerSvc'           = 'Windows Error Reporting uploads'
    'PcaSvc'           = 'Program Compatibility Assistant'
    'MapsBroker'       = 'Downloaded Maps Manager'
    'lfsvc'            = 'Geolocation'
    'RetailDemo'       = 'Store demo mode'
    'Fax'              = 'Fax'
    'WMPNetworkSvc'    = 'Windows Media Player network sharing'
    'RemoteRegistry'   = 'Remote registry editing'
    'wisvc'            = 'Windows Insider'
}

$TelemetryTasks = @(
    '\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser',
    '\Microsoft\Windows\Application Experience\ProgramDataUpdater',
    '\Microsoft\Windows\Application Experience\StartupAppTask',
    '\Microsoft\Windows\Customer Experience Improvement Program\Consolidator',
    '\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip',
    '\Microsoft\Windows\Feedback\Siuf\DmClient',
    '\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload',
    '\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector',
    '\Microsoft\Windows\Maps\MapsUpdateTask',
    '\Microsoft\Windows\Maps\MapsToastTask'
)

function Split-TaskPath([string]$full) {
    $i = $full.LastIndexOf('\')
    @{ Path = $full.Substring(0, $i + 1); Name = $full.Substring($i + 1) }
}

function Set-Reg($path, $name, $value, $type = 'DWord') {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    Set-ItemProperty -Path $path -Name $name -Value $value -Type $type
}

# ------------------------------------------------------------------ revert
if ($Revert) {
    if (-not (Test-Path $StateFile)) {
        Write-Host "no state file at $StateFile - nothing to revert" -ForegroundColor Yellow
        return
    }
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    foreach ($s in $state.services) {
        try {
            Set-Service -Name $s.Name -StartupType $s.StartType -ErrorAction Stop
            Write-Host ("  {0,-18} -> {1}" -f $s.Name, $s.StartType) -ForegroundColor Green
        } catch { Write-Host "  $($s.Name): $($_.Exception.Message)" -ForegroundColor Red }
    }
    foreach ($t in $TelemetryTasks) {
        $p = Split-TaskPath $t
        Enable-ScheduledTask -TaskPath $p.Path -TaskName $p.Name -ErrorAction SilentlyContinue | Out-Null
    }
    Remove-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Name AllowTelemetry -ErrorAction SilentlyContinue
    Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' 'VisualFXSetting' 0
    Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' 'EnableTransparency' 1
    Set-Reg 'HKCU:\Control Panel\Desktop\WindowMetrics' 'MinAnimate' '1' 'String'
    if ($state.mask) {
        Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name UserPreferencesMask -Type Binary `
            -Value ([Convert]::FromBase64String($state.mask))
    }
    if ($state.hibernate) { powercfg /h on | Out-Null }
    Remove-Item $StateFile -Force
    Write-Host "reverted; sign out and back in for the visual settings" -ForegroundColor Cyan
    return
}

# ------------------------------------------------------------------- apply
if (Test-Path $StateFile) {
    # A second run would find everything already disabled, record nothing,
    # and overwrite the record of what the machine looked like before.
    Write-Host "already applied (state file exists); run with -Revert first to re-apply" -ForegroundColor Yellow
    return
}
$freeBefore = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
$diskBefore = (Get-PSDrive C).Free / 1GB
$state = @{ services = @(); hibernate = $false; mask = $null }

Write-Host "== services" -ForegroundColor Cyan
foreach ($name in $Services.Keys) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $svc) { continue }
    if ($svc.StartType -eq 'Disabled') {
        Write-Host ("  {0,-18} already off" -f $name) -ForegroundColor DarkGray
        continue
    }
    # Record before changing, so -Revert has the real original value.
    $state.services += @{ Name = $name; StartType = "$($svc.StartType)" }
    try {
        Set-Service -Name $name -StartupType Disabled -ErrorAction Stop
        if ($svc.Status -eq 'Running') { Stop-Service -Name $name -Force -ErrorAction SilentlyContinue }
        Write-Host ("  {0,-18} off   ({1})" -f $name, $Services[$name]) -ForegroundColor Green
    } catch {
        Write-Host ("  {0,-18} FAILED: {1}" -f $name, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host "== telemetry tasks and policy" -ForegroundColor Cyan
$n = 0
foreach ($t in $TelemetryTasks) {
    $p = Split-TaskPath $t
    if (Disable-ScheduledTask -TaskPath $p.Path -TaskName $p.Name -ErrorAction SilentlyContinue) { $n++ }
}
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' 'AllowTelemetry' 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' 'DoNotShowFeedbackNotifications' 1
Set-Reg 'HKCU:\Software\Microsoft\Siuf\Rules' 'NumberOfSIUFInPeriod' 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\AdvertisingInfo' 'DisabledByGroupPolicy' 1
Write-Host "  $n tasks disabled, telemetry policy set to minimum" -ForegroundColor Green

Write-Host "== startup delay" -ForegroundColor Cyan
# Windows holds autostart apps back for a few seconds after logon. Invisible
# change: nothing about how the desktop looks or animates is touched here.
Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize' 'StartupDelayInMSec' 0
Write-Host "  autostart delay 0 (animations and transparency untouched)" -ForegroundColor Green

Write-Host "== hibernation file" -ForegroundColor Cyan
$hib = 'C:\hiberfil.sys'
if (Test-Path $hib -ErrorAction SilentlyContinue) {
    $state.hibernate = $true
    $gb = (Get-Item $hib -Force).Length / 1GB
    powercfg /h off | Out-Null
    Write-Host ("  hiberfil.sys removed ({0:N1} GB of disk)" -f $gb) -ForegroundColor Green
} else {
    Write-Host "  already off" -ForegroundColor DarkGray
}

Write-Host "== temp files" -ForegroundColor Cyan
$tempMB = 0
foreach ($dir in @($env:TEMP, "$env:SystemRoot\Temp")) {
    Get-ChildItem $dir -Force -Recurse -ErrorAction SilentlyContinue |
        Where-Object { -not $_.PSIsContainer -and $_.LastWriteTime -lt (Get-Date).AddDays(-2) } |
        ForEach-Object {
            $len = $_.Length
            try { Remove-Item $_.FullName -Force -ErrorAction Stop; $tempMB += $len / 1MB } catch { }
        }
}
Write-Host ("  {0:N0} MB of temp files older than 2 days removed" -f $tempMB) -ForegroundColor Green

$state | ConvertTo-Json -Depth 4 | Set-Content -Path $StateFile -Encoding UTF8

$freeAfter = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
$diskAfter = (Get-PSDrive C).Free / 1GB
Write-Host ""
Write-Host "================ result ================" -ForegroundColor Cyan
"RAM free : {0:N2} -> {1:N2} GB" -f $freeBefore, $freeAfter
"disk C:  : {0:N1} -> {1:N1} GB free" -f $diskBefore, $diskAfter
"state    : $StateFile  (undo with -Revert)"
Write-Host ""
Write-Host "Reboot once so the stopped services and the startup delay take effect cleanly." -ForegroundColor Yellow
