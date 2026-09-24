# Read-only: when the machine lags, which resource is saturated and who is
# using it. Samples CPU, memory and the system disk for ~10 seconds.
# Run it WHILE the lag is happening; a calm moment shows a calm machine.
#
#   powershell -ExecutionPolicy Bypass -File lag_report.ps1
#
# WMI performance classes instead of Get-Counter: counter paths are
# localized on a Russian Windows, WMI class and property names are not.
#
# On a 2-core laptop the report itself is a real load, so it stays light:
# processes are read twice (raw CPU time at the start and at the end)
# instead of on every sample, only the needed columns are fetched, and the
# report's own share is shown apart instead of topping the list.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

$ErrorActionPreference = 'SilentlyContinue'
$Samples = 5
$Interval = 2
$cores = [int](Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$nominal = [int](Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed

# Work Windows does by itself after updates or a long time switched off:
# heavy while it runs, gone once it is done.
$Maintenance = @{
    'TiWorker'              = 'Windows Update installing'
    'TrustedInstaller'      = 'Windows Update installing'
    'MoUsoCoreWorker'       = 'Windows Update'
    'wuauclt'               = 'Windows Update'
    'SIHClient'             = 'Windows Update'
    'WaaSMedicAgent'        = 'Windows Update repair'
    'MsMpEng'               = 'Defender scan or signature update'
    'MpDefenderCoreService' = 'Defender'
    'MpCmdRun'              = 'Defender scan or signature update'
    'mscorsvw'              = '.NET recompiling after an update'
    'ngen'                  = '.NET recompiling after an update'
    'SearchIndexer'         = 'search indexing'
    'SearchProtocolHost'    = 'search indexing'
    'SearchFilterHost'      = 'search indexing'
    'CompatTelRunner'       = 'compatibility scan'
    'DismHost'              = 'component cleanup'
}

function Get-Snap {
    [PSCustomObject]@{
        Cpu  = Get-CimInstance -Query "SELECT PercentProcessorTime, PercentDPCTime, PercentInterruptTime FROM Win32_PerfFormattedData_PerfOS_Processor WHERE Name='_Total'"
        Perf = Get-CimInstance -Query "SELECT PercentProcessorPerformance FROM Win32_PerfFormattedData_Counters_ProcessorInformation WHERE Name='_Total'"
        Sys  = Get-CimInstance -Query "SELECT ProcessorQueueLength FROM Win32_PerfFormattedData_PerfOS_System"
        Mem  = Get-CimInstance -Query "SELECT AvailableMBytes, PagesInputPersec, PagesOutputPersec FROM Win32_PerfFormattedData_PerfOS_Memory"
        Disk = @(Get-CimInstance -Query "SELECT Name, PercentIdleTime, DiskReadBytesPersec, DiskWriteBytesPersec FROM Win32_PerfFormattedData_PerfDisk_PhysicalDisk" |
                 Where-Object { $_.Name -match 'C:' })[0]
    }
}

# Raw CPU time of every process. The difference between two reads divided
# by the time between them is the exact average over the whole window.
function Get-ProcTimes {
    $rows = Get-CimInstance -Query "SELECT Name, IDProcess, PercentProcessorTime, Timestamp_Sys100NS FROM Win32_PerfRawData_PerfProc_Process"
    $map = @{}
    $ts = [long]0
    foreach ($r in $rows) {
        if (-not $ts) { $ts = [long]$r.Timestamp_Sys100NS }
        if ($r.Name -eq '_Total' -or $r.Name -eq 'Idle') { continue }
        $base = $r.Name -replace '#\d+$', ''
        # Keyed by PID and name: the #N suffixes shift as processes come and go.
        $map["$($r.IDProcess)|$base"] = [PSCustomObject]@{
            Id = [int]$r.IDProcess; Name = $base; Time = [long]$r.PercentProcessorTime
        }
    }
    [PSCustomObject]@{ Ts = $ts; Procs = $map }
}

function Avg($values) { ($values | Measure-Object -Average).Average }

Write-Host "sampling for $($Samples * $Interval) seconds - keep doing whatever lags..." -ForegroundColor Cyan
$p0 = Get-ProcTimes
# Formatted perf classes compute rates between two reads; the first read
# primes them and returns zeros.
Get-Snap | Out-Null
$snaps = foreach ($i in 1..$Samples) { Start-Sleep $Interval; Get-Snap }
$p1 = Get-ProcTimes

# ------------------------------------------------------------ processes
$span = [double]($p1.Ts - $p0.Ts) * $cores
$byName = @{}
$self = 0.0
if ($span -gt 0) {
    foreach ($k in $p1.Procs.Keys) {
        $e = $p1.Procs[$k]
        $s = $p0.Procs[$k]
        # Not there at the start means it started inside the window, so all
        # of its CPU time was spent inside it.
        $d = if ($s) { $e.Time - $s.Time } else { $e.Time }
        if ($d -le 0) { continue }
        $pct = 100.0 * $d / $span
        if ($e.Id -eq $PID) { $self += $pct; continue }
        $byName[$e.Name] = $byName[$e.Name] + $pct
    }
}
$top = @($byName.GetEnumerator() | Where-Object { $_.Value -ge 0.5 } |
         Sort-Object Value -Descending | Select-Object -First 12)

# --------------------------------------------------------------------- cpu
$cpuAvg = Avg ($snaps | ForEach-Object { [double]$_.Cpu.PercentProcessorTime })
$cpuMax = ($snaps | ForEach-Object { [double]$_.Cpu.PercentProcessorTime } | Measure-Object -Maximum).Maximum
$dpc = Avg ($snaps | ForEach-Object { [double]$_.Cpu.PercentDPCTime })
$irq = Avg ($snaps | ForEach-Object { [double]$_.Cpu.PercentInterruptTime })
$queue = Avg ($snaps | ForEach-Object { [double]$_.Sys.ProcessorQueueLength })
$perf = Avg ($snaps | ForEach-Object { [double]$_.Perf.PercentProcessorPerformance })
$others = [Math]::Max(0, $cpuAvg - $self)

Write-Host ""
Write-Host "=== cpu ($cores logical, nominal $nominal MHz) ===" -ForegroundColor Cyan
"load       : {0:N0}% average, {1:N0}% peak; without this report {2:N0}%" -f $cpuAvg, $cpuMax, $others
"clock      : {0:N0}% of nominal = about {1:N0} MHz" -f $perf, ($nominal * $perf / 100)
"run queue  : {0:N1} threads waiting (more than {1} means the CPU cannot keep up)" -f $queue, (2 * $cores)
"drivers    : DPC {0:N1}%, interrupts {1:N1}% (above 5% points at a driver)" -f $dpc, $irq

Write-Host ""
Write-Host "=== top processes by CPU (share of the whole machine) ===" -ForegroundColor Cyan
foreach ($t in $top) {
    $note = if ($t.Key -eq 'WmiPrvSE') { '  (partly this report)' }
            elseif ($Maintenance.ContainsKey($t.Key)) { "  ($($Maintenance[$t.Key]))" }
            else { '' }
    "{0,-32} {1,5:N1}%{2}" -f $t.Key, $t.Value, $note
}
if ($p1.Procs.Count -eq 0) {
    Write-Host "process list unavailable - run from an administrator PowerShell" -ForegroundColor Yellow
} else {
    "{0,-32} {1,5:N1}%  (this report, not in the list)" -f 'powershell', $self
}

# ------------------------------------------------------------ memory, disk
$avail = Avg ($snaps | ForEach-Object { [double]$_.Mem.AvailableMBytes })
$pageMB = Avg ($snaps | ForEach-Object { ([double]$_.Mem.PagesInputPersec + [double]$_.Mem.PagesOutputPersec) * 4KB / 1MB })
# A sample without the disk would read as 0% idle, i.e. a false 100% busy.
$diskSnaps = @($snaps | Where-Object { $_.Disk })
$diskActive = Avg ($diskSnaps | ForEach-Object { 100 - [double]$_.Disk.PercentIdleTime })
$diskMB = Avg ($diskSnaps | ForEach-Object { ([double]$_.Disk.DiskReadBytesPersec + [double]$_.Disk.DiskWriteBytesPersec) / 1MB })

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
if ($others -ge 85 -or $queue -ge 2 * $cores) {
    Write-Host "CPU is saturated: the processes at the top of the CPU list are the lag." -ForegroundColor Yellow
    $found = $true
}
$busy = @($top | Where-Object { $Maintenance.ContainsKey($_.Key) -and $_.Value -ge 10 })
if ($busy.Count -gt 0) {
    $what = ($busy | ForEach-Object { "{0} ({1})" -f $_.Key, $Maintenance[$_.Key] }) -join ', '
    Write-Host "Windows is busy with its own maintenance: $what." -ForegroundColor Yellow
    Write-Host "  Temporary: leave the laptop on the charger and let it finish." -ForegroundColor Yellow
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
