# Keeps a memory-starved laptop breathing without anyone typing commands.
#
# Run with -Install once: registers a scheduled task that runs this same file
# at logon and every 15 minutes afterwards, as SYSTEM so no console window
# ever flashes. Run with -Status to read what it has been doing.
#
# Two jobs per pass:
#   1. Drop duplicate MCP server processes. Claude Desktop restarts a server
#      without killing the previous one, so they pile up - 40 GitKraken
#      processes where 2 belong. The newest one is the live one.
#   2. Trim working sets, but ONLY when free memory is under the threshold.
#      Doing it on a timer regardless would make applications re-fault their
#      pages for no reason.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the system ANSI codepage, so
# Cyrillic here would become mojibake and break the parser.

[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [double]$FreeGBThreshold = 1.5
)

$ErrorActionPreference = 'Continue'
$TaskName = 'ClaudeTune'
$LogFile = Join-Path (Split-Path -Parent $PSCommandPath) 'autotune.log'
$LogKeepLines = 300

function Write-Log([string]$text) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
        # Keep the log from growing without bound: every pass appends a line,
        # which is ~35k lines a year.
        $lines = @(Get-Content $LogFile -ErrorAction Stop)
        if ($lines.Count -gt ($LogKeepLines * 2)) {
            $tail = $lines[-$LogKeepLines..-1]
            [IO.File]::WriteAllLines($LogFile, $tail, (New-Object Text.UTF8Encoding $false))
        }
    } catch { }
    Write-Verbose $line
}

function Get-FreeGB {
    (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
}

# ---------------------------------------------------------------- install
function Install-Task {
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $ps `
        -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath)

    $atLogon = New-ScheduledTaskTrigger -AtLogOn
    # Repetition needs a finite duration on PowerShell 5.1; ten years is
    # "forever" for this purpose.
    $every15 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes 15) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -Hidden `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    # SYSTEM: no window, no stored password, and enough rights to trim the
    # working set of processes belonging to the logged-on user.
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
        -LogonType ServiceAccount -RunLevel Highest

    Register-ScheduledTask -TaskName $TaskName -Action $action `
        -Trigger $atLogon, $every15 -Settings $settings -Principal $principal `
        -Description 'Trims memory and clears duplicate MCP servers' -Force | Out-Null

    Write-Host "task '$TaskName' registered" -ForegroundColor Green
    Write-Host "  script : $PSCommandPath"
    Write-Host "  log    : $LogFile"
    Write-Host "  runs   : at logon, then every 15 minutes, as SYSTEM, no window"
    Write-Host ""
    Write-Host "running one pass now..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 12
    Show-Status
}

function Uninstall-Task {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "task '$TaskName' removed" -ForegroundColor Yellow
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "task '$TaskName' is not installed; run this file with -Install" -ForegroundColor Yellow
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        "task    : {0}" -f $task.State
        "last run: {0} (result {1})" -f $info.LastRunTime, $info.LastTaskResult
        "next run: {0}" -f $info.NextRunTime
    }
    "free now: {0:N2} GB" -f (Get-FreeGB)
    Write-Host ""
    if (Test-Path $LogFile) {
        Write-Host "--- last 15 log lines ---"
        Get-Content $LogFile -Tail 15
    } else {
        Write-Host "(no log yet)"
    }
}

# ------------------------------------------------------------------- work
function Remove-DuplicateMcpServers {
    $all = Get-CimInstance Win32_Process -Filter "Name='node.exe' or Name='gk.exe'" -ErrorAction SilentlyContinue
    if (-not $all) { return 0 }

    $tagged = foreach ($p in $all) {
        $cl = "$($p.CommandLine)"
        $family =
            if ($p.Name -eq 'gk.exe' -or $cl -match 'gitkraken|run-gk') { 'gitkraken' }
            elseif ($cl -match 'railway')           { 'railway' }
            elseif ($cl -match 'desktop-commander') { 'desktop-commander' }
            elseif ($cl -match 'server-pdf')        { 'server-pdf' }
            else { $null }
        if ($family) {
            [PSCustomObject]@{ Id = $p.ProcessId; Family = $family; Born = $p.CreationDate }
        }
    }
    if (-not $tagged) { return 0 }

    $killed = 0
    foreach ($group in ($tagged | Group-Object Family)) {
        $newest = ($group.Group | Sort-Object Born -Descending)[0].Born
        foreach ($proc in $group.Group) {
            # A server and its npx wrapper start within seconds of each other,
            # so the recent cluster is the live one and everything older is a
            # leftover from a restart.
            if (($newest - $proc.Born).TotalSeconds -gt 60) {
                try {
                    Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                    $killed++
                } catch { }
            }
        }
    }
    return $killed
}

function Compress-WorkingSets {
    if (-not ('MemTrim' -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class MemTrim {
  [DllImport("psapi.dll")] public static extern bool EmptyWorkingSet(IntPtr hProcess);
}
"@
    }
    $freed = 0
    foreach ($p in Get-Process) {
        try {
            $before = $p.WorkingSet64
            [MemTrim]::EmptyWorkingSet($p.Handle) | Out-Null
            $p.Refresh()
            $freed += ($before - $p.WorkingSet64)
        } catch { }
    }
    return [math]::Round($freed / 1MB)
}

function Invoke-Pass {
    $before = Get-FreeGB
    $killed = Remove-DuplicateMcpServers

    if ($before -lt $FreeGBThreshold) {
        $freedMB = Compress-WorkingSets
        $after = Get-FreeGB
        Write-Log ("free {0:N2} -> {1:N2} GB, trimmed {2} MB, killed {3} stale MCP" -f `
            $before, $after, $freedMB, $killed)
    } else {
        Write-Log ("free {0:N2} GB, above threshold {1:N2}, no trim, killed {2} stale MCP" -f `
            $before, $FreeGBThreshold, $killed)
    }
}

# ------------------------------------------------------------------ entry
if ($Install)   { Install-Task;   return }
if ($Uninstall) { Uninstall-Task; return }
if ($Status)    { Show-Status;    return }
Invoke-Pass
