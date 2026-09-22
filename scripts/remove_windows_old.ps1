# Removes C:\Windows.old, the previous Windows kept for rollback after a
# feature update. Irreversible: afterwards there is no going back to the old
# build.
#
# Guard: a user profile under Windows.old\Users with no counterpart under
# C:\Users holds files that exist nowhere else. The script lists such
# profiles and stops instead of deleting. Copy what matters, then rerun
# with -Force.
#
# Removal goes through Disk Cleanup's own "Previous Installations" handler,
# which is built to get past TrustedInstaller ownership. Only if that leaves
# the folder behind does it fall back to a backup-mode robocopy purge.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Continue'
$Old = 'C:\Windows.old'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "NOT ELEVATED - run from an administrator PowerShell." -ForegroundColor Red
    Write-Host ("  Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-File','{0}'" -f $PSCommandPath)
    return
}

if (-not (Test-Path $Old)) {
    Write-Host "no C:\Windows.old on this machine - nothing to remove" -ForegroundColor Green
    return
}

function Get-FreeGB { (Get-PSDrive C).Free / 1GB }

function Get-TreeMB([string]$path) {
    $sum = (Get-ChildItem $path -Recurse -Force -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    [math]::Round($sum / 1MB)
}

# ------------------------------------------------------------------- guard
Write-Host "== checking for profiles that exist only inside Windows.old" -ForegroundColor Cyan
$orphans = @()
$oldUsers = Join-Path $Old 'Users'
if (Test-Path $oldUsers) {
    $skip = @('Public', 'Default', 'Default User', 'All Users')
    foreach ($u in Get-ChildItem $oldUsers -Directory -Force -ErrorAction SilentlyContinue) {
        if ($skip -contains $u.Name -or $u.Name -like 'defaultuser*') { continue }
        # Junctions such as "All Users" point back into the live system.
        if ($u.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        if (Test-Path (Join-Path 'C:\Users' $u.Name)) {
            Write-Host ("  {0,-16} also in C:\Users - migrated, fine" -f $u.Name) -ForegroundColor DarkGray
            continue
        }

        # Personal data only: AppData is almost entirely caches, and the
        # legacy junctions inside a profile (My Documents, Cookies, ...) are
        # reparse points that would double-count or loop.
        $items = @()
        foreach ($c in Get-ChildItem $u.FullName -Force -ErrorAction SilentlyContinue) {
            if ($c.Name -eq 'AppData') { continue }
            if ($c.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            $mb = if ($c.PSIsContainer) { Get-TreeMB $c.FullName } else { [math]::Round($c.Length / 1MB) }
            if ($mb -ge 1) {
                $git = $c.PSIsContainer -and (Test-Path (Join-Path $c.FullName '.git'))
                $items += [PSCustomObject]@{ Name = $c.Name; MB = $mb; Repo = $(if ($git) { 'git' } else { '' }) }
            }
        }
        $total = ($items | Measure-Object MB -Sum).Sum
        if ($total -ge 5) {
            $orphans += [PSCustomObject]@{ Name = $u.Name; Path = $u.FullName; MB = $total; Items = $items }
        } else {
            Write-Host ("  {0,-16} only here, but empty ({1} MB) - fine" -f $u.Name, [int]$total) -ForegroundColor DarkGray
        }
    }
}

if ($orphans -and -not $Force) {
    Write-Host ""
    Write-Host "STOPPED: these profiles exist ONLY inside Windows.old." -ForegroundColor Yellow
    Write-Host "Deleting it would delete these files for good." -ForegroundColor Yellow
    foreach ($o in $orphans) {
        Write-Host ""
        Write-Host ("  {0}  ({1:N0} MB of personal data, AppData excluded)" -f $o.Name, $o.MB) -ForegroundColor Yellow
        $o.Items | Sort-Object MB -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String | Write-Host
        $dest = Join-Path $env:USERPROFILE ("old-profile-" + $o.Name)
        Write-Host "  copy it out (junctions skipped, AppData skipped):"
        Write-Host ("  robocopy `"{0}`" `"{1}`" /E /XJ /XD AppData /R:0 /W:0 /NFL /NDL" -f $o.Path, $dest)
    }
    Write-Host ""
    Write-Host "Then run this again with -Force:" -ForegroundColor Yellow
    Write-Host ("  Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-File','{0}','-Force'" -f $PSCommandPath)
    return
}

# ------------------------------------------------------------------ remove
$before = Get-FreeGB
$started = Get-Date
Write-Host ""
Write-Host "== removing via Disk Cleanup (Previous Installations)" -ForegroundColor Cyan

$vc = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches'
$flag = 'StateFlags0042'
$handlers = @('Previous Installations', 'Temporary Setup Files', 'Windows Upgrade Log Files', 'Setup Log Files')
$cleanmgr = Join-Path $env:SystemRoot 'System32\cleanmgr.exe'

if (Test-Path $cleanmgr) {
    # Preset 42 runs only the handlers flagged with StateFlags0042; clear any
    # stale ones first so nothing else gets swept up.
    Get-ChildItem $vc -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-ItemProperty $_.PSPath -Name $flag -ErrorAction SilentlyContinue }
    foreach ($h in $handlers) {
        if (Test-Path "$vc\$h") { Set-ItemProperty "$vc\$h" -Name $flag -Value 2 -Type DWord }
    }
    Write-Host "  running cleanmgr /sagerun:42 - a progress window will appear; this machine may need 10-30 min"
    Start-Process $cleanmgr -ArgumentList '/sagerun:42' -Wait
    Wait-Process -Name cleanmgr -Timeout 2700 -ErrorAction SilentlyContinue
    foreach ($h in $handlers) { Remove-ItemProperty "$vc\$h" -Name $flag -ErrorAction SilentlyContinue }
} else {
    Write-Host "  cleanmgr.exe not present on this build, skipping to fallback" -ForegroundColor Yellow
}

if (Test-Path $Old) {
    Write-Host "== fallback: backup-mode purge" -ForegroundColor Cyan
    # Mirroring an empty folder onto Windows.old deletes its contents. /B
    # opens files with backup semantics, which administrators hold, so
    # TrustedInstaller-owned files do not block it.
    $empty = Join-Path $env:TEMP ('empty-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $empty -Force | Out-Null
    robocopy $empty $Old /MIR /B /R:0 /W:0 /NFL /NDL /NJH /NJS | Out-Null
    Remove-Item $empty -Force -ErrorAction SilentlyContinue
    # The now-empty root still belongs to TrustedInstaller.
    takeown /F $Old /A 2>&1 | Out-Null
    icacls $Old /grant '*S-1-5-32-544:F' 2>&1 | Out-Null
    Remove-Item $Old -Recurse -Force -ErrorAction SilentlyContinue
}

$after = Get-FreeGB
$mins = ((Get-Date) - $started).TotalMinutes
Write-Host ""
Write-Host "================ result ================" -ForegroundColor Cyan
if (Test-Path $Old) {
    $left = @(Get-ChildItem $Old -Recurse -Force -ErrorAction SilentlyContinue).Count
    Write-Host ("C:\Windows.old still present, {0} items left" -f $left) -ForegroundColor Red
    Write-Host "  finish by hand: Settings > System > Storage > Temporary files > Previous Windows installation(s)"
} else {
    Write-Host "C:\Windows.old removed" -ForegroundColor Green
}
"disk C: free {0:N1} -> {1:N1} GB  (+{2:N1} GB, {3:N0} min)" -f $before, $after, ($after - $before), $mins
