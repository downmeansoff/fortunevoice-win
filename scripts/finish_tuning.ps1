# Closes out the laptop tuning session: password, shell history, repo
# hygiene, leftover model weights. Every block reports OK or FAIL and none
# of them stops the others.
#
# ASCII only on purpose: PowerShell 5.1 reads .ps1 in the system ANSI
# codepage, so Cyrillic in a UTF-8 file becomes mojibake and breaks the
# parser before the first line runs.
#
#   powershell -ExecutionPolicy Bypass -File finish_tuning.ps1
#
# Needs an elevated shell for the password change; the rest works without.

$ErrorActionPreference = 'Continue'
$results = [ordered]@{}

function Step([string]$name, [scriptblock]$body) {
    Write-Host ""
    Write-Host "== $name" -ForegroundColor Cyan
    try {
        & $body
        $results[$name] = 'OK'
    } catch {
        Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
        $results[$name] = "FAIL: $($_.Exception.Message)"
    }
}

$elevated = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "elevated: $elevated"

# ---------------------------------------------------------------- password
Step "password for account $env:USERNAME" {
    if (-not $elevated) {
        throw "needs an elevated PowerShell (Run as administrator)"
    }

    Write-Host "  Enter a new password, or press Enter to have one generated."
    $sec = Read-Host "  new password" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }

    $generated = $false
    if ([string]::IsNullOrWhiteSpace($plain)) {
        # Ambiguous glyphs left out: this gets retyped on another machine to
        # remap the network drive, and 0/O l/1/I at a keyboard is a support call.
        $alphabet = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_=+'
        $bytes = New-Object byte[] 20
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $plain = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
        $generated = $true
    }

    if ($plain.Length -lt 8) {
        throw "password shorter than 8 characters, refusing (SMB network logon is open on this account)"
    }

    # The account running this, whatever it is called. Hardcoding a name
    # breaks the moment the machine is reinstalled under a different one.
    $user = [ADSI]"WinNT://$env:COMPUTERNAME/$env:USERNAME,user"
    $user.SetPassword($plain)
    $user.SetInfo()
    Write-Host "  password changed" -ForegroundColor Green

    if ($generated) {
        $file = Join-Path $env:USERPROFILE 'new-password.txt'
        [IO.File]::WriteAllText($file, $plain, (New-Object Text.UTF8Encoding $false))
        Write-Host "  generated one, written to $file" -ForegroundColor Yellow
        Write-Host "  SAVE IT IN A PASSWORD MANAGER, THEN DELETE THAT FILE." -ForegroundColor Yellow
    }
    $plain = $null
}

# ----------------------------------------------------------------- history
Step "PowerShell history" {
    $path = (Get-PSReadlineOption).HistorySavePath
    if (-not (Test-Path $path)) {
        Write-Host "  no history file at $path"
        return
    }
    $before = @(Get-Content $path -Encoding UTF8)
    # The plaintext 'net user HP 1951' and anything else quoting that password.
    $keep = $before | Where-Object { $_ -notmatch 'net user|1951' }
    [IO.File]::WriteAllLines($path, $keep, (New-Object Text.UTF8Encoding $false))
    Write-Host ("  {0} -> {1} lines ({2} removed)" -f `
        $before.Count, $keep.Count, ($before.Count - $keep.Count)) -ForegroundColor Green
}

# -------------------------------------------------------------------- repos
function Sync-Repo([string]$path, [string[]]$branches) {
    if (-not (Test-Path (Join-Path $path '.git'))) {
        Write-Host "  no clone at $path, skipping"
        return
    }
    Push-Location $path
    try {
        $dirty = git status --porcelain
        if ($dirty) {
            Write-Host "  local changes present, leaving the worktree alone:" -ForegroundColor Yellow
            $dirty | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
        }
        $head = (git symbolic-ref --short HEAD 2>$null)
        if ($head -ne 'main' -and -not $dirty) {
            git checkout main 2>&1 | Out-Null
        }
        git fetch origin 2>&1 | Out-Null
        if (-not $dirty) {
            git pull --ff-only origin main 2>&1 | Out-Null
        }
        Write-Host ("  at {0}" -f (git log --oneline -1)) -ForegroundColor Green

        foreach ($branch in $branches) {
            $exists = git ls-remote --heads origin $branch
            if (-not $exists) {
                Write-Host "  $branch already gone"
                continue
            }
            # Only delete what main already contains. An unmerged branch here
            # would be somebody's work, not leftovers.
            $merged = git branch -r --merged origin/main --list "origin/$branch"
            if (-not $merged) {
                Write-Host "  $branch is NOT merged into main, keeping it" -ForegroundColor Yellow
                continue
            }
            git push origin --delete $branch 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  deleted $branch" -ForegroundColor Green
            } else {
                Write-Host "  could not delete $branch (exit $LASTEXITCODE)" -ForegroundColor Red
            }
        }
    } finally {
        Pop-Location
    }
}

function Find-CloneOf([string]$repoName) {
    # Path of the clone whose origin ends in <repoName>, or $null. Searched
    # rather than hardcoded: the clone moves when the machine is reinstalled
    # under a different account, and a stale path here would silently skip
    # the whole step.
    $roots = @($env:USERPROFILE, (Join-Path $env:USERPROFILE 'dev'),
               (Join-Path $env:USERPROFILE 'Projects'), (Join-Path $env:USERPROFILE 'source\repos'))
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($dir in (Get-ChildItem $root -Directory -ErrorAction SilentlyContinue)) {
            if (-not (Test-Path (Join-Path $dir.FullName '.git'))) { continue }
            $url = git -C $dir.FullName remote get-url origin 2>$null
            if ($url -and $url -match "[/:].*$([regex]::Escape($repoName))(\.git)?/?$") {
                return $dir.FullName
            }
        }
    }
    return $null
}

Step "repo fortunevoice-win" {
    $path = Find-CloneOf 'fortunevoice-win'
    if (-not $path) {
        Write-Host "  no local clone found; delete the merged branches at"
        Write-Host "  https://github.com/downmeansoff/fortunevoice-win/branches"
        return
    }
    Write-Host "  found at $path"
    Sync-Repo $path @('fix/setup-installs-package', 'bench/decode-options')
}

Step "repo VPN" {
    $path = Find-CloneOf 'VPN'
    if (-not $path) {
        Write-Host "  no local VPN clone found; delete ci/guard-bare-push at"
        Write-Host "  https://github.com/downmeansoff/VPN/branches"
        return
    }
    Write-Host "  found at $path"
    Sync-Repo $path @('ci/guard-bare-push')
}

# ------------------------------------------------------------------ cleanup
Step "leftover weights and bench files" {
    $models = Join-Path $env:APPDATA 'FortuneVoice\models'
    if (Test-Path $models) {
        # small is both FVModel and FVFallbackModel, so tiny and base are only
        # here because the benchmark pulled them.
        Get-ChildItem $models -Directory |
            Where-Object { $_.Name -match 'tiny|base' } |
            ForEach-Object {
                $mb = [math]::Round(
                    (Get-ChildItem $_.FullName -Recurse -File |
                     Measure-Object Length -Sum).Sum / 1MB)
                Remove-Item $_.FullName -Recurse -Force
                Write-Host ("  removed {0} ({1} MB)" -f $_.Name, $mb) -ForegroundColor Green
            }
        $left = Get-ChildItem $models -Directory | Select-Object -ExpandProperty Name
        Write-Host ("  kept: {0}" -f ($left -join ', '))
    }

    Get-ChildItem (Join-Path $env:USERPROFILE 'claude-tuning') -Filter 'bench*' -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-Item $_.FullName -Force -Recurse
            Write-Host "  removed $($_.Name)" -ForegroundColor Green
        }
}

# ------------------------------------------------------------------- report
Write-Host ""
Write-Host "================ state ================" -ForegroundColor Cyan

$phys = (Get-CimInstance Win32_PhysicalMemory | Measure-Object Capacity -Sum).Sum / 1GB
$os = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB
$free = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
"memory   : {0:N2} GB installed / {1:N2} GB to Windows / {2:N2} GB to graphics / {3:N2} GB free now" -f `
    $phys, $os, ($phys - $os), $free

$secureBoot = try { Confirm-SecureBootUEFI } catch { 'unknown (needs elevation)' }
"secureboot: $secureBoot"

$cfg = Join-Path $env:APPDATA 'FortuneVoice\config.json'
if (Test-Path $cfg) {
    $json = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
    "fortunevoice: model=$($json.FVModel) device=$($json.FVDevice) streaming=$($json.FVStreaming) hotkey=$($json.FVHotkey)"
}

Write-Host ""
Write-Host "================ steps ================" -ForegroundColor Cyan
foreach ($k in $results.Keys) {
    $colour = if ($results[$k] -eq 'OK') { 'Green' } else { 'Red' }
    Write-Host ("{0,-34} {1}" -f $k, $results[$k]) -ForegroundColor $colour
}

Write-Host ""
Write-Host "Left for you by hand:" -ForegroundColor Yellow
Write-Host "  - Secure Boot back to Enabled in the BIOS (F10 at boot) if it reads False above"
Write-Host "  - remap Z: on the second PC, the old credentials stopped working"
