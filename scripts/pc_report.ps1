# Read-only snapshot of a Windows machine for tuning: hardware, what eats
# memory, what starts with the system, and which of the usual knobs are
# already set. Changes nothing.
#
#   powershell -ExecutionPolicy Bypass -File pc_report.ps1
#
# Run it elevated for the full picture: Defender details need admin.
# The report is also saved to the Desktop as pc_report_<COMPUTERNAME>.txt.
#
# ASCII only: PowerShell 5.1 reads .ps1 in the ANSI codepage.

$ErrorActionPreference = 'SilentlyContinue'
$Lines = New-Object System.Collections.Generic.List[string]

function Out-Line([string]$text = '') { $Lines.Add($text); Write-Host $text }
function Out-Section([string]$title) { Out-Line ''; Out-Line "=== $title ===" }

# Last two 0x........ values in a powercfg query are the AC and DC indexes;
# matching the hex avoids depending on the localized labels around them.
function Get-PowerIndex([string]$sub, [string]$setting) {
    $hex = [regex]::Matches(((powercfg /query SCHEME_CURRENT $sub $setting) -join ' '), '0x[0-9a-fA-F]{8}')
    if ($hex.Count -ge 2) {
        '{0}/{1}' -f [Convert]::ToInt32($hex[$hex.Count - 2].Value, 16), [Convert]::ToInt32($hex[$hex.Count - 1].Value, 16)
    } else { 'n/a' }
}

$elevated = (New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem

# ------------------------------------------------------------------ system
Out-Section 'system'
# ProductName keeps saying "Windows 10" after an upgrade to 11; the build
# number is what tells them apart.
$win = if ([int]$cv.CurrentBuild -ge 22000) { 'Windows 11' } else { 'Windows 10' }
Out-Line ("{0} {1}, build {2}.{3}, edition {4}" -f $win, $cv.DisplayVersion, $cv.CurrentBuild, $cv.UBR, $cv.EditionID)
Out-Line ("host {0}, user {1}, elevated {2}, uptime {3:N1} h" -f `
    $env:COMPUTERNAME, $env:USERNAME, $elevated, ((Get-Date) - $os.LastBootUpTime).TotalHours)

# ---------------------------------------------------------------- hardware
Out-Section 'hardware'
foreach ($c in Get-CimInstance Win32_Processor) {
    Out-Line ("CPU   {0}, {1} cores / {2} threads, base {3} MHz" -f `
        $c.Name.Trim(), $c.NumberOfCores, $c.NumberOfLogicalProcessors, $c.MaxClockSpeed)
}
$dimms = @(Get-CimInstance Win32_PhysicalMemory)
$installed = ($dimms | Measure-Object Capacity -Sum).Sum / 1GB
Out-Line ("RAM   {0:N1} GB installed in {1} module(s), {2:N2} GB visible to Windows, {3:N2} GB free now" -f `
    $installed, $dimms.Count, ($cs.TotalPhysicalMemory / 1GB), ($os.FreePhysicalMemory / 1MB))
foreach ($d in $dimms) {
    Out-Line ("      {0} GB running at {1} MHz, rated {2} MHz, slot {3}" -f `
        ($d.Capacity / 1GB), $d.ConfiguredClockSpeed, $d.Speed, $d.DeviceLocator)
}
foreach ($g in Get-CimInstance Win32_VideoController) {
    Out-Line ("GPU   {0}, driver {1} from {2:yyyy-MM-dd}" -f $g.Name, $g.DriverVersion, $g.DriverDate)
}
foreach ($p in Get-PhysicalDisk) {
    Out-Line ("Disk  {0}, {1} {2}, {3:N0} GB, health {4}" -f `
        $p.FriendlyName, $p.MediaType, $p.BusType, ($p.Size / 1GB), $p.HealthStatus)
}
foreach ($v in (Get-Volume | Where-Object { $_.DriveLetter -and $_.Size -gt 0 })) {
    Out-Line ("Vol   {0}: {1:N1} of {2:N1} GB free, {3}" -f `
        $v.DriveLetter, ($v.SizeRemaining / 1GB), ($v.Size / 1GB), $v.FileSystem)
}
foreach ($n in (Get-NetAdapter | Where-Object Status -eq 'Up')) {
    Out-Line ("Net   {0}: {1}, {2}" -f $n.Name, $n.LinkSpeed, $n.InterfaceDescription)
}

# ------------------------------------------------------------------- power
Out-Section 'power'
Out-Line ((powercfg /getactivescheme) -join ' ')
Out-Line ("processor min/max state AC/DC: {0} / {1} %, boost mode AC/DC: {2}" -f `
    (Get-PowerIndex SUB_PROCESSOR PROCTHROTTLEMIN), (Get-PowerIndex SUB_PROCESSOR PROCTHROTTLEMAX),
    (Get-PowerIndex SUB_PROCESSOR PERFBOOSTMODE))
Out-Line ("hibernation file: {0}" -f $(
    $h = Get-Item 'C:\hiberfil.sys' -Force
    if ($h) { '{0:N1} GB' -f ($h.Length / 1GB) } else { 'none' }))

# ---------------------------------------------------------- memory manager
Out-Section 'memory manager'
$mm = Get-MMAgent
# Get-MMAgent is admin-only; without elevation it returns nothing, which
# printed as blanks that read like "off".
$mc = if ($mm) { $mm.MemoryCompression } else { 'needs admin' }
$pc = if ($mm) { $mm.PageCombining } else { 'needs admin' }
Out-Line ("MemoryCompression {0}, PageCombining {1}, SysMain {2}" -f $mc, $pc, (Get-Service SysMain).Status)
if ($cs.AutomaticManagedPagefile) {
    Out-Line 'pagefile: managed by Windows'
} else {
    foreach ($f in Get-CimInstance Win32_PageFileSetting) {
        Out-Line ("pagefile: {0} {1}-{2} MB" -f $f.Name, $f.InitialSize, $f.MaximumSize)
    }
}
foreach ($u in Get-CimInstance Win32_PageFileUsage) {
    Out-Line ("pagefile use: {0} MB now, {1} MB peak, {2} MB allocated" -f $u.CurrentUsage, $u.PeakUsage, $u.AllocatedBaseSize)
}
Out-Line ("commit charge: {0:N1} of {1:N1} GB" -f `
    (($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB), ($os.TotalVirtualMemorySize / 1MB))

# --------------------------------------------------------------- processes
Out-Section 'top 15 by private memory'
Get-Process | Group-Object ProcessName | ForEach-Object {
    [PSCustomObject]@{
        Name = $_.Name
        MB   = [math]::Round(($_.Group | Measure-Object PrivateMemorySize64 -Sum).Sum / 1MB)
        N    = $_.Count
    }
} | Sort-Object MB -Descending | Select-Object -First 15 | ForEach-Object {
    Out-Line ("{0,-30} {1,6} MB  x{2}" -f $_.Name, $_.MB, $_.N)
}

# ---------------------------------------------------------------- autostart
Out-Section 'autostart entries'
foreach ($s in Get-CimInstance Win32_StartupCommand) {
    Out-Line ("{0}  <-  {1}" -f $s.Name, $s.Command)
}
Out-Section 'enabled scheduled tasks outside \Microsoft\'
foreach ($t in (Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and $_.TaskPath -notlike '\Microsoft\*' })) {
    Out-Line ("{0}{1}  <-  {2}" -f $t.TaskPath, $t.TaskName, (($t.Actions | ForEach-Object { $_.Execute }) -join '; '))
}

# ----------------------------------------------------------------- services
Out-Section 'services worth a look'
$watch = 'DiagTrack', 'dmwappushservice', 'WerSvc', 'PcaSvc', 'MapsBroker', 'lfsvc', 'RetailDemo',
         'Fax', 'WMPNetworkSvc', 'RemoteRegistry', 'wisvc', 'WSearch', 'Spooler', 'SysMain',
         'XblAuthManager', 'XblGameSave', 'XboxNetApiSvc', 'XboxGipSvc', 'TabletInputService'
foreach ($n in $watch) {
    $s = Get-Service -Name $n
    if ($s) { Out-Line ("{0,-20} {1,-8} {2}" -f $n, $s.Status, $s.StartType) }
}
Out-Section 'third-party services running'
Get-CimInstance Win32_Service -Filter "State='Running'" |
    Where-Object { $_.PathName -and $_.PathName -notmatch '\\Windows\\(System32|SysWOW64|servicing)\\|\\Microsoft\\|\\Windows Defender' } |
    Sort-Object Name | ForEach-Object { Out-Line ("{0,-32} {1}" -f $_.Name, $_.DisplayName) }

# -------------------------------------------------------- defender / telemetry
Out-Section 'defender and telemetry'
$mp = Get-MpComputerStatus
if ($mp) {
    $excl = if ($elevated) { @((Get-MpPreference).ExclusionPath | Where-Object { $_ }).Count } else { 'needs admin' }
    Out-Line ("Defender realtime {0}, tamper protection {1}, path exclusions {2}" -f `
        $mp.RealTimeProtectionEnabled, $mp.IsTamperProtected, $excl)
} else {
    Out-Line 'Defender status unavailable (third-party antivirus?)'
}
$tel = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection').AllowTelemetry
Out-Line ("AllowTelemetry policy: {0}" -f $(if ($null -eq $tel) { 'not set' } else { $tel }))

# ---------------------------------------------------------- graphics / games
Out-Section 'graphics and gaming'
$hags = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers').HwSchMode
$gm = (Get-ItemProperty 'HKCU:\Software\Microsoft\GameBar').AutoGameModeEnabled
$dvr = (Get-ItemProperty 'HKCU:\System\GameConfigStore').GameDVR_Enabled
Out-Line ("HW GPU scheduling {0} (2 = on), Game Mode {1}, Game DVR {2}" -f `
    $(if ($null -eq $hags) { 'n/a' } else { $hags }),
    $(if ($null -eq $gm) { 'default' } else { $gm }),
    $(if ($null -eq $dvr) { 'default' } else { $dvr }))

# ---------------------------------------------------------------- leftovers
Out-Section 'disk leftovers'
Out-Line ("Windows.old present: {0}" -f (Test-Path 'C:\Windows.old'))
$tmp = (Get-ChildItem $env:TEMP -Recurse -Force -File | Measure-Object Length -Sum).Sum
Out-Line ("user TEMP: {0:N0} MB" -f ($tmp / 1MB))

$file = Join-Path ([Environment]::GetFolderPath('Desktop')) ("pc_report_{0}.txt" -f $env:COMPUTERNAME)
[IO.File]::WriteAllLines($file, $Lines, (New-Object Text.UTF8Encoding $false))
Write-Host ''
Write-Host "saved to $file" -ForegroundColor Green
