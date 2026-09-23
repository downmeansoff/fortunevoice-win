# Read-only: why is the system disk busy? Samples disk activity, paging and
# per-process I/O for ~10 seconds, then sizes the usual space hogs on C:.
# Changes nothing.
#
#   powershell -ExecutionPolicy Bypass -File disk_report.ps1
#
# Uses the WMI performance classes rather than Get-Counter: counter paths are
# localized on a Russian Windows, the WMI class and property names are not.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

$ErrorActionPreference = 'SilentlyContinue'
$Samples = 5
$Interval = 2

function Get-Snap {
    [PSCustomObject]@{
        Disk  = @(Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk |
                  Where-Object { $_.Name -match 'C:' })[0]
        Mem   = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
        Procs = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
                Where-Object { $_.Name -notin '_Total', 'Idle' }
    }
}

function Get-SizeGB([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    $item = Get-Item $path -Force
    $bytes = if ($item.PSIsContainer) {
        (Get-ChildItem $path -Recurse -File -Force | Measure-Object Length -Sum).Sum
    } else { $item.Length }
    [math]::Round($bytes / 1GB, 1)
}

Write-Host "sampling for $($Samples * $Interval) seconds..." -ForegroundColor Cyan
# Formatted perf classes compute rates between two reads; the first read
# primes them and returns zeros.
Get-Snap | Out-Null
Start-Sleep 1
$snaps = foreach ($i in 1..$Samples) { Get-Snap; Start-Sleep $Interval }

# -------------------------------------------------------------------- disk
Write-Host ""
Write-Host "=== disk C: ===" -ForegroundColor Cyan
$active = ($snaps | ForEach-Object { 100 - [double]$_.Disk.PercentIdleTime } | Measure-Object -Average -Maximum)
$readMB = ($snaps | ForEach-Object { [double]$_.Disk.DiskReadBytesPersec / 1MB } | Measure-Object -Average).Average
$writeMB = ($snaps | ForEach-Object { [double]$_.Disk.DiskWriteBytesPersec / 1MB } | Measure-Object -Average).Average
$queue = ($snaps | ForEach-Object { [double]$_.Disk.CurrentDiskQueueLength } | Measure-Object -Average).Average
"active time: {0:N0}% average, {1:N0}% peak" -f $active.Average, $active.Maximum
"throughput : read {0:N1} MB/s, write {1:N1} MB/s, queue {2:N1}" -f $readMB, $writeMB, $queue

# ------------------------------------------------------------------ paging
Write-Host ""
Write-Host "=== paging ===" -ForegroundColor Cyan
# Pages are 4 KB. Pages Input = read back from the pagefile or mapped files
# on hard faults; Pages Output = written out to make room.
$inMB = ($snaps | ForEach-Object { [double]$_.Mem.PagesInputPersec * 4KB / 1MB } | Measure-Object -Average).Average
$outMB = ($snaps | ForEach-Object { [double]$_.Mem.PagesOutputPersec * 4KB / 1MB } | Measure-Object -Average).Average
$last = $snaps[-1].Mem
"paging in {0:N1} MB/s, paging out {1:N1} MB/s" -f $inMB, $outMB
"available {0:N0} MB, committed {1:N1} of {2:N1} GB" -f `
    $last.AvailableMBytes, ($last.CommittedBytes / 1GB), ($last.CommitLimit / 1GB)
if (($inMB + $outMB) -gt 0 -and ($readMB + $writeMB) -gt 0) {
    "paging is about {0:N0}% of all disk traffic" -f ((($inMB + $outMB) / ($readMB + $writeMB)) * 100)
}

# --------------------------------------------------------------- processes
Write-Host ""
Write-Host "=== top processes by I/O (disk and network together) ===" -ForegroundColor Cyan
$snaps | ForEach-Object { $_.Procs } |
    Group-Object { $_.Name -replace '#\d+$', '' } |
    ForEach-Object {
        $r = ($_.Group | Measure-Object IOReadBytesPersec -Sum).Sum / $Samples / 1MB
        $w = ($_.Group | Measure-Object IOWriteBytesPersec -Sum).Sum / $Samples / 1MB
        [PSCustomObject]@{ Name = $_.Name; Read = $r; Write = $w; Total = $r + $w }
    } |
    Sort-Object Total -Descending | Select-Object -First 12 |
    ForEach-Object { "{0,-30} read {1,7:N1} MB/s   write {2,7:N1} MB/s" -f $_.Name, $_.Read, $_.Write }

# ------------------------------------------------------------------- space
Write-Host ""
Write-Host "=== what fills C: ===" -ForegroundColor Cyan
$c = Get-PSDrive C
"C: {0:N1} GB free of {1:N1} GB ({2:N0}% full)" -f ($c.Free / 1GB), (($c.Used + $c.Free) / 1GB), ($c.Used / ($c.Used + $c.Free) * 100)
$spots = [ordered]@{
    'pagefile.sys'              = 'C:\pagefile.sys'
    'swapfile.sys'              = 'C:\swapfile.sys'
    'Docker / WSL disks'        = "$env:LOCALAPPDATA\Docker"
    'WSL distros (new layout)'  = "$env:LOCALAPPDATA\wsl"
    'Claude app data'           = "$env:APPDATA\Claude"
    'HuggingFace cache'         = "$env:USERPROFILE\.cache\huggingface"
    'FortuneVoice models'       = "$env:APPDATA\FortuneVoice\models"
    'Ollama models'             = "$env:USERPROFILE\.ollama"
    'pip cache'                 = "$env:LOCALAPPDATA\pip\cache"
    'npm cache'                 = "$env:LOCALAPPDATA\npm-cache"
    'Windows Update downloads'  = 'C:\Windows\SoftwareDistribution\Download'
    'Downloads'                 = "$env:USERPROFILE\Downloads"
}
foreach ($k in $spots.Keys) {
    $gb = Get-SizeGB $spots[$k]
    if ($null -ne $gb -and $gb -ge 0.5) { "{0,-26} {1,6:N1} GB" -f $k, $gb }
}
# WSL distros installed from the Store keep their disk under Packages.
Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Force |
    ForEach-Object { Get-ChildItem (Join-Path $_.FullName 'LocalState') -Filter *.vhdx -Force } |
    ForEach-Object { "{0,-26} {1,6:N1} GB" -f ("WSL " + $_.Directory.Parent.Name.Split('_')[0]), ($_.Length / 1GB) }

Write-Host ""
"TRIM: " + ((fsutil behavior query DisableDeleteNotify) -join ' | ')
