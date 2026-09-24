# Read-only: when the machine lags, which resource is saturated and who is
# using it. Samples CPU, memory and the system disk for ~10 seconds.
# Run it WHILE the lag is happening; a calm moment shows a calm machine.
#
#   powershell -ExecutionPolicy Bypass -File lag_report.ps1
#
# WMI performance classes instead of Get-Counter: counter paths are
# localized on a Russian Windows, WMI class and property names are not.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

$ErrorActionPreference = 'SilentlyContinue'
$Samples = 5
$Interval = 2
$cores = [int](Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$nominal = [int](Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed

function Get-Snap {
    [PSCustomObject]@{
        Cpu   = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
        Perf  = Get-CimInstance Win32_PerfFormattedData_Counters_ProcessorInformation -Filter "Name='_Total'"
        Sys   = Get-CimInstance Win32_PerfFormattedData_PerfOS_System
        Mem   = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
        Disk  = @(Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk |
                  Where-Object { $_.Name -match 'C:' })[0]
        Procs = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
                Where-Object { $_.Name -notin '_Total', 'Idle' }
    }
}

function Avg($values) { ($values | Measure-Object -Average).Average }

Write-Host "sampling for $($Samples * $Interval) seconds - keep doing whatever lags..." -ForegroundColor Cyan
# Formatted perf classes compute rates between two reads; the first read
# primes them and returns zeros.
Get-Snap | Out-Null
Start-Sleep 1
$snaps = foreach ($i in 1..$Samples) { Get-Snap; Start-Sleep $Interval }

# --------------------------------------------------------------------- cpu
$cpuAvg = Avg ($snaps | ForEach-Object { [double]$_.Cpu.PercentProcessorTime })
$cpuMax = ($snaps | ForEach-Object { [double]$_.Cpu.PercentProcessorTime } | Measure-Object -Maximum).Maximum
$dpc = Avg ($snaps | ForEach-Object { [double]$_.Cpu.PercentDPCTime })
$irq = Avg ($snaps | ForEach-Object { [double]$_.Cpu.PercentInterruptTime })
$queue = Avg ($snaps | ForEach-Object { [double]$_.Sys.ProcessorQueueLength })
$perf = Avg ($snaps | ForEach-Object { [double]$_.Perf.PercentProcessorPerformance })

Write-Host ""
Write-Host "=== cpu ($cores logical, nominal $nominal MHz) ===" -ForegroundColor Cyan
"load       : {0:N0}% average, {1:N0}% peak" -f $cpuAvg, $cpuMax
"clock      : {0:N0}% of nominal = about {1:N0} MHz" -f $perf, ($nominal * $perf / 100)
"run queue  : {0:N1} threads waiting (more than {1} means the CPU cannot keep up)" -f $queue, (2 * $cores)
"drivers    : DPC {0:N1}%, interrupts {1:N1}% (above 5% points at a driver)" -f $dpc, $irq

Write-Host ""
Write-Host "=== top processes by CPU (share of the whole machine) ===" -ForegroundColor Cyan
$snaps | ForEach-Object { $_.Procs } |
    Group-Object { $_.Name -replace '#\d+$', '' } |
    ForEach-Object {
        [PSCustomObject]@{
            Name = $_.Name
            Cpu  = ($_.Group | Measure-Object PercentProcessorTime -Sum).Sum / $Samples / $cores
        }
    } |
    Where-Object { $_.Cpu -ge 0.5 } |
    Sort-Object Cpu -Descending | Select-Object -First 12 |
    ForEach-Object { "{0,-32} {1,5:N1}%" -f $_.Name, $_.Cpu }

# ------------------------------------------------------------ memory, disk
$avail = Avg ($snaps | ForEach-Object { [double]$_.Mem.AvailableMBytes })
$pageMB = Avg ($snaps | ForEach-Object { ([double]$_.Mem.PagesInputPersec + [double]$_.Mem.PagesOutputPersec) * 4KB / 1MB })
$diskActive = Avg ($snaps | ForEach-Object { 100 - [double]$_.Disk.PercentIdleTime })
$diskMB = Avg ($snaps | ForEach-Object { ([double]$_.Disk.DiskReadBytesPersec + [double]$_.Disk.DiskWriteBytesPersec) / 1MB })

Write-Host ""
Write-Host "=== memory and disk ===" -ForegroundColor Cyan
"memory     : {0:N0} MB available, paging {1:N1} MB/s" -f $avail, $pageMB
"disk C:    : {0:N0}% active, {1:N1} MB/s" -f $diskActive, $diskMB

# ----------------------------------------------------- security features
# Memory integrity (HVCI) is emulated on CPUs without mode-based execution
# control, older AMD Zen included, and can cost a noticeable share of a
# small CPU. Reported, never changed.
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard
if ($dg) {
    $vbs = switch ([int]$dg.VirtualizationBasedSecurityStatus) { 0 { 'off' } 1 { 'enabled, not running' } 2 { 'running' } default { 'unknown' } }
    $hvci = if (@($dg.SecurityServicesRunning) -contains 2) { 'running' } else { 'off' }
    Write-Host ""
    Write-Host "=== virtualization-based security ===" -ForegroundColor Cyan
    "VBS {0}, memory integrity (HVCI) {1}" -f $vbs, $hvci
}

# ----------------------------------------------------------------- verdict
Write-Host ""
Write-Host "=== verdict ===" -ForegroundColor Cyan
$found = $false
if ($cpuAvg -ge 85 -or $queue -ge 2 * $cores) {
    Write-Host "CPU is saturated: the processes at the top of the CPU list are the lag." -ForegroundColor Yellow
    $found = $true
}
if ($dpc + $irq -ge 10) {
    Write-Host "Driver time is high: a device driver is stealing the CPU." -ForegroundColor Yellow
    $found = $true
}
if ($avail -lt 500 -or $pageMB -ge 10) {
    Write-Host "Memory is short: the machine is paging." -ForegroundColor Yellow
    $found = $true
}
if ($diskActive -ge 80) {
    $why = if ($diskMB -gt 0 -and $pageMB / $diskMB -ge 0.5) { ' - mostly paging, so really a memory problem' } else { '' }
    Write-Host ("Disk is saturated{0}." -f $why) -ForegroundColor Yellow
    $found = $true
}
if (-not $found) {
    Write-Host "Nothing was saturated during the sample. Run it again while the lag is happening." -ForegroundColor Green
}
