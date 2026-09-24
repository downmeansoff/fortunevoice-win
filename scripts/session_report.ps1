# Where the memory goes on a machine that runs many Claude Code sessions.
# Every process is counted under the session that started it; what closed
# sessions and shells left running is listed apart; the rest of the machine
# is grouped by application. Read-only unless -StopLeftovers is given.
#
#   powershell -ExecutionPolicy Bypass -File session_report.ps1
#   powershell -ExecutionPolicy Bypass -File session_report.ps1 -StopLeftovers
#
# -StopLeftovers stops only leftovers of the throwaway kind that are older
# than two hours: static file servers (python -m http.server), scripts run
# from the temp folder, sandbox scripts, Playwright browsers, and shells
# that were started to run a command and now run nothing. A leftover dev
# server or long job is listed and left for you to decide.
#
# -Quiet prints nothing and returns those throwaway leftovers as objects;
# autotune.ps1 uses it with -StopLeftovers on every pass.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

[CmdletBinding()]
param([switch]$StopLeftovers, [switch]$Quiet, [double]$MinAgeHours = 2)

$ErrorActionPreference = 'SilentlyContinue'
$now = Get-Date

# Always-on tools of this machine: never leftovers, whatever their parent.
$Keep = 'fortunevoice|ClaudeSync|CodexTelegramBridge|ClipFix|autotune\.ps1|FortuneVPN'
$Shells = @('powershell.exe', 'pwsh.exe', 'cmd.exe', 'bash.exe', 'sh.exe', 'nohup.exe')
$Terminals = @('powershell.exe', 'pwsh.exe', 'cmd.exe', 'bash.exe', 'WindowsTerminal.exe', 'OpenConsole.exe', 'Code.exe')
$DevTools = $Shells + @('python.exe', 'pythonw.exe', 'node.exe', 'uv.exe', 'uvicorn.exe', 'esbuild.exe')

$procs = @(Get-CimInstance Win32_Process)
$byId = @{}
foreach ($p in $procs) { $byId[[int]$p.ProcessId] = $p }

# A "parent" that started after the child is a newer process that reused
# the PID: the real parent is gone.
function Get-Parent($p) {
    $pp = $byId[[int]$p.ParentProcessId]
    if ($pp -and $pp.ProcessId -ne $p.ProcessId -and $pp.CreationDate -le $p.CreationDate) { return $pp }
    return $null
}

$kids = @{}
foreach ($p in $procs) {
    $pp = Get-Parent $p
    if (-not $pp) { continue }
    $k = [int]$pp.ProcessId
    if (-not $kids.ContainsKey($k)) { $kids[$k] = New-Object System.Collections.ArrayList }
    [void]$kids[$k].Add($p)
}

function Test-Session($p) {
    $cmd = "$($p.CommandLine)"
    if ($p.Name -eq 'node.exe') { return $cmd -match 'claude-code[\\/]cli' }
    if ($p.Name -ne 'claude.exe' -or $cmd -match '--type=') { return $false }
    # Run by Claude Desktop, which talks to the session over stream-json.
    if ($cmd -match 'stream-json') { return $true }
    # Started by hand in a terminal.
    $pp = Get-Parent $p
    return [bool]($pp -and $Terminals -contains $pp.Name)
}

# Everything below a process, not descending into a nested session.
function Get-Tree($root) {
    $out = New-Object System.Collections.ArrayList
    $stack = New-Object System.Collections.Stack
    $stack.Push($root)
    while ($stack.Count) {
        $p = $stack.Pop()
        foreach ($c in @($kids[[int]$p.ProcessId])) {
            if (-not $c -or (Test-Session $c)) { continue }
            [void]$out.Add($c)
            $stack.Push($c)
        }
    }
    return , $out
}

function Get-MB($list) { [double](@($list) | Measure-Object PrivatePageCount -Sum).Sum / 1MB }

function Format-Age($p) {
    $t = $now - $p.CreationDate
    if ($t.TotalHours -ge 1) { return '{0}h{1:00}m' -f [int][math]::Floor($t.TotalHours), $t.Minutes }
    return '{0}m' -f [int][math]::Floor($t.TotalMinutes)
}

# The command without the path of the program that runs it.
function Get-Short($p) {
    $name = $p.Name -replace '\.exe$', ''
    $args_ = "$($p.CommandLine)" -replace '^\s*("[^"]*"|\S+)\s*', '' -replace '\s+', ' '
    $s = "$name $args_".Trim()
    if ($s.Length -gt 72) { $s = $s.Substring(0, 69) + '...' }
    return $s
}

function Test-Throwaway($p) {
    $c = "$($p.CommandLine)"
    return ($c -match '-m\s+http\.server' -or $c -match '\\AppData\\Local\\Temp[\\/]' -or
            $c -match '_sandbox_\w*\.py' -or "$($p.ExecutablePath)" -match 'ms-playwright')
}

# A shell someone typed into has no command of its own. Only shells started
# to run something, now with nothing under them, count as idle.
function Test-IdleShell($p, $tree) {
    if ($Shells -notcontains $p.Name) { return $false }
    if ("$($p.CommandLine)" -notmatch '\s-(\w*c|Command|File|EncodedCommand|NonInteractive)(\s|$)') { return $false }
    return -not @($tree | Where-Object { $_.Name -ne 'conhost.exe' }).Count
}

# Memory figures are taken before anything is stopped.
if (-not $Quiet) {
    $os = Get-CimInstance Win32_OperatingSystem
    $pf = @(Get-CimInstance Win32_PageFileUsage)
    $mc = Get-Process -Name 'Memory Compression' -ErrorAction SilentlyContinue
    $av = @(Get-CimInstance -Namespace root\SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object { $_.displayName })
}

# ---------------------------------------------------------------- sessions
$claimed = @{}
$rows = @(foreach ($s in @($procs | Where-Object { Test-Session $_ } | Sort-Object CreationDate)) {
    $tree = Get-Tree $s
    $claimed[[int]$s.ProcessId] = $true
    foreach ($c in $tree) { $claimed[[int]$c.ProcessId] = $true }
    $what = @($tree | Where-Object { $_.Name -ne 'conhost.exe' } | Group-Object Name |
              Sort-Object Count -Descending | ForEach-Object { '{0} x{1}' -f ($_.Name -replace '\.exe$', ''), $_.Count })
    [PSCustomObject]@{
        Proc   = $s
        Effort = $(if ("$($s.CommandLine)" -match '--effort\s+(\S+)') { $Matches[1] } else { '' })
        Own    = Get-MB $s
        Runs   = Get-MB $tree
        What   = $what -join ', '
        Big    = @($tree | Sort-Object PrivatePageCount -Descending | Select-Object -First 1)
    }
})

# --------------------------------------------------------------- leftovers
$left = @(foreach ($p in $procs) {
    if ($claimed[[int]$p.ProcessId] -or (Get-Parent $p)) { continue }
    if ($DevTools -notcontains $p.Name -and "$($p.ExecutablePath)" -notmatch 'ms-playwright') { continue }
    $tree = Get-Tree $p
    $members = @($p) + @($tree)
    if (@($members | Where-Object { "$($_.CommandLine)" -match $Keep }).Count) { continue }
    foreach ($m in $members) { $claimed[[int]$m.ProcessId] = $true }
    [PSCustomObject]@{
        Root      = $p
        Members   = $members
        MB        = Get-MB $members
        Throwaway = (Test-IdleShell $p $tree) -or [bool]@($members | Where-Object { Test-Throwaway $_ }).Count
        Old       = ($now - $p.CreationDate).TotalHours -ge $MinAgeHours
        Main      = @($members | Where-Object { $_.Name -ne 'conhost.exe' } | Sort-Object PrivatePageCount -Descending)[0]
    }
})
$stale = @($left | Where-Object { $_.Throwaway -and $_.Old })
if ($StopLeftovers) {
    foreach ($l in $stale) {
        foreach ($m in $l.Members) { Stop-Process -Id $m.ProcessId -Force -ErrorAction SilentlyContinue }
    }
}
if ($Quiet) {
    foreach ($l in $stale) {
        [PSCustomObject]@{ Pid = $l.Root.ProcessId; MB = $l.MB; Age = Format-Age $l.Root; Command = Get-Short $l.Main }
    }
    return
}

# ------------------------------------------------------------------ report
Write-Host "=== memory ===" -ForegroundColor Cyan
"RAM         : {0:N1} GB, available {1:N0} MB" -f ($os.TotalVisibleMemorySize / 1MB), ($os.FreePhysicalMemory / 1KB)
"committed   : {0:N1} GB of {1:N1} GB possible" -f (($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB), ($os.TotalVirtualMemorySize / 1MB)
"page file   : {0:N0} MB allocated, {1:N0} MB in use" -f ($pf | Measure-Object AllocatedBaseSize -Sum).Sum, ($pf | Measure-Object CurrentUsage -Sum).Sum
if ($mc) {
    "compression : on, {0:N0} MB of RAM holds compressed pages" -f ($mc.WorkingSet64 / 1MB)
} else {
    Write-Host "compression : OFF - in an administrator PowerShell: Enable-MMAgent -MemoryCompression" -ForegroundColor Yellow
}
if ($av.Count) { "antivirus   : " + ($av -join ', ') }

$ownGB = ($rows | Measure-Object Own -Sum).Sum / 1KB
$runsGB = ($rows | Measure-Object Runs -Sum).Sum / 1KB
Write-Host ""
Write-Host ("=== Claude Code sessions: {0}, {1:N1} GB themselves + {2:N1} GB in what they run ===" -f $rows.Count, $ownGB, $runsGB) -ForegroundColor Cyan
if ($rows.Count) { "  PID  age     effort session   +runs  what they run" }
foreach ($r in $rows) {
    "{0,5} {1,-7} {2,-6} {3,5:N0} MB {4,5:N0} MB  {5}" -f $r.Proc.ProcessId, (Format-Age $r.Proc), $r.Effort, $r.Own, $r.Runs, $r.What
    if ($r.Big.Count -and (Get-MB $r.Big) -ge 200) {
        "{0,35}biggest: {1:N0} MB {2}" -f '', (Get-MB $r.Big), (Get-Short $r.Big[0])
    }
}

Write-Host ""
Write-Host ("=== left running after their session or shell closed: {0}, {1:N0} MB ===" -f $left.Count, ($left | Measure-Object MB -Sum).Sum) -ForegroundColor Cyan
foreach ($l in @($left | Sort-Object MB -Descending)) {
    $tag = if ($l.Throwaway) { '  [throwaway]' } else { '' }
    "{0,5} {1,-7} {2,5:N0} MB  {3}{4}" -f $l.Root.ProcessId, (Format-Age $l.Root), $l.MB, (Get-Short $l.Main), $tag
}
if ($StopLeftovers) {
    Write-Host ("stopped {0} throwaway leftovers older than {1} h, about {2:N0} MB" -f $stale.Count, $MinAgeHours, ($stale | Measure-Object MB -Sum).Sum) -ForegroundColor Green
} elseif ($stale.Count) {
    Write-Host ("{0} [throwaway] older than {1} h, about {2:N0} MB: stop them with -StopLeftovers" -f $stale.Count, $MinAgeHours, ($stale | Measure-Object MB -Sum).Sum) -ForegroundColor Yellow
}

# -------------------------------------------------------- rest of machine
function Get-App($p) {
    if ("$($p.CommandLine)" -match 'fortunevoice') { return 'FortuneVoice' }
    $q = $p
    for ($i = 0; $q -and $i -lt 12; $i++) {
        if ($q.Name -eq 'claude.exe') { return 'Claude Desktop + its MCP servers' }
        $q = Get-Parent $q
    }
    if ($p.Name -match '^(vmmem|com\.docker\.|Docker Desktop|docker|wsl|vpnkit)') { return 'Docker / WSL' }
    return $p.Name -replace '\.exe$', ''
}

Write-Host ""
Write-Host "=== the rest of the machine, top 12 ===" -ForegroundColor Cyan
$procs | Where-Object { -not $claimed[[int]$_.ProcessId] -and $_.ProcessId -gt 4 } |
    Group-Object { Get-App $_ } |
    ForEach-Object { [PSCustomObject]@{ Name = $_.Name; MB = Get-MB $_.Group; N = $_.Count } } |
    Sort-Object MB -Descending | Select-Object -First 12 |
    ForEach-Object { "{0,-34} {1,6:N0} MB  x{2}" -f $_.Name, $_.MB, $_.N }
