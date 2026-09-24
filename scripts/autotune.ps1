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
#   2. Stop throwaway leftovers of closed Claude Code sessions older than
#      two hours, through session_report.ps1 next to this file: static file
#      servers, scripts run from the temp folder, sandbox scripts, Playwright
#      browsers, idle command shells. Each one is written to the log.
#
# It used to trim every working set when free memory ran low. Under
# sustained pressure that only pushed pages out to the page file and pulled
# them back, adding to the very disk load it was meant to relieve; Windows
# trims working sets on its own when it needs to.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the system ANSI codepage, so
# Cyrillic here would become mojibake and break the parser.

[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status
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
function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Install-Task {
    # Registering a SYSTEM-principal task needs administrator rights, and
    # without this check Register-ScheduledTask fails with a bare access
    # denied while the rest of the function happily reports success.
    if (-not (Test-Elevated)) {
        Write-Host "NOT ELEVATED - open PowerShell as administrator and run this again." -ForegroundColor Red
        Write-Host "  From a normal window, this elevates and installs in one step:"
        Write-Host ("  Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-File','{0}','-Install'" -f $PSCommandPath)
        return
    }

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

    # SYSTEM: no window, no stored password, and enough rights to stop
    # processes belonging to the logged-on user.
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
        -LogonType ServiceAccount -RunLevel Highest

    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action `
            -Trigger $atLogon, $every15 -Settings $settings -Principal $principal `
            -Description 'Clears duplicate MCP servers and leftovers of closed Claude Code sessions' `
            -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Host "FAILED to register the task: $($_.Exception.Message)" -ForegroundColor Red
        return
    }

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

function Stop-Leftovers {
    $report = Join-Path (Split-Path -Parent $PSCommandPath) 'session_report.ps1'
    if (-not (Test-Path $report)) { return @() }
    return @(& $report -StopLeftovers -Quiet)
}

function Invoke-Pass {
    $killed = Remove-DuplicateMcpServers
    $left = Stop-Leftovers
    Write-Log ("free {0:N2} GB, killed {1} stale MCP, stopped {2} leftovers ({3:N0} MB)" -f `
        (Get-FreeGB), $killed, $left.Count, [double]($left | Measure-Object MB -Sum).Sum)
    foreach ($l in $left) {
        Write-Log ("  stopped {0}, {1:N0} MB, {2} old: {3}" -f $l.Pid, $l.MB, $l.Age, $l.Command)
    }
}

# ------------------------------------------------------------------ entry
if ($Install)   { Install-Task;   return }
if ($Uninstall) { Uninstall-Task; return }
if ($Status)    { Show-Status;    return }
Invoke-Pass
