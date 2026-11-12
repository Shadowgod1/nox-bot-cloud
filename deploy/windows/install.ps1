<#
.SYNOPSIS
    ILER Bot installer for Windows (WSL2 + Ubuntu 24.04 + Docker Engine inside WSL).

.DESCRIPTION
    Spec section 8 (Windows). Supported: Windows 10 22H2, Windows 11, Windows Server 2022+.
    Steps: admin/virtualization checks -> enable WSL + VirtualMachinePlatform (RunOnce resume after
    reboot) -> install Ubuntu-24.04 without interaction -> systemd on -> copy the bundle into WSL and
    run deploy/install.sh -> networking (mirrored on Win11 22H2+, otherwise netsh portproxy refreshed at
    boot) -> firewall rule for the local subnet only -> boot task that keeps the distro running ->
    desktop shortcut to the admin UI.

.PARAMETER BundlePath
    Folder that contains compose.yml and deploy\ (default: two levels above this script).
.PARAMETER Port
    Admin UI port on the LAN (default 8080).
.PARAMETER Offline
    Path to images.tar (docker save) for installation without the registry.
.PARAMETER AutoReboot
    Reboot automatically when Windows features require it (otherwise the user is asked).
.PARAMETER Resume
    Internal: set by the RunOnce entry after a reboot.
.PARAMETER KeysFile
    Internal (set by the GUI installer): KEY=VALUE lines with ILER's LLM keys and the monthly coin limit
    entered by the technician. Moved to an admin-only file under ProgramData until applied, then deleted.
.PARAMETER ProgressFile
    Internal (set by the GUI installer): the script runs hidden and reports "percent|stage|detail" lines
    to this file; the last line is "done|<url>", "error|<message>" or "reboot|".

Windows PowerShell 5.1 compatible. Log: C:\ProgramData\ILER\install.log
#>
[CmdletBinding()]
param(
    [string]$BundlePath = '',
    [ValidateRange(1, 65535)][int]$Port = 8080,
    [string]$Offline = '',
    [switch]$AutoReboot,
    [switch]$Resume,
    [string]$ProgressFile = '',
    [string]$KeysFile = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Distro = 'Ubuntu-24.04'
$DistroLauncher = 'ubuntu2404.exe'
$LinuxUser = 'iler'
$WslBundleDir = '/root/iler-bundle'
$DataDir = Join-Path $env:ProgramData 'ILER'
$LogFile = Join-Path $DataDir 'install.log'
$TaskStart = 'ILER Bot - start WSL'
$TaskPortProxy = 'ILER Bot - refresh portproxy'
$FirewallRule = 'ILER Bot admin UI'
$RunOnceName = 'ILERSetupResume'
$ShortcutName = 'ILER Bot - админка.url'
$PendingKeys = Join-Path $DataDir 'pending-keys.env'
$KeyVars = @('ILER_ANTHROPIC_KEY', 'ILER_OPENAI_KEY', 'ILER_GEMINI_KEY', 'ILER_MONTHLY_COINS')
# Sources allowed to reach the admin port (RFC1918, loopback, IPv6 ULA / link-local / loopback)
$PrivateRanges = @('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', 'fc00::/7', 'fe80::/10', '::1')

# wsl.exe prints UTF-16 unless told otherwise
$env:WSL_UTF8 = '1'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host " ok $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Warning $Message }

function Write-Progress2([string]$Line) {
    # Status for the GUI installer; it only reads the last line. Never fails the install.
    if (-not $ProgressFile) { return }
    try { [IO.File]::AppendAllText($ProgressFile, "$Line`r`n", (New-Object System.Text.UTF8Encoding($false))) }
    catch { Write-Verbose "progress file: $($_.Exception.Message)" }
}
function Set-Stage([int]$Percent, [string]$Stage) { Write-Progress2 "$Percent|$Stage|" }

function Stop-Install([string]$Message) {
    Write-Progress2 ("error|" + ($Message -replace '[\r\n|]+', ' '))
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host "Log: $LogFile"
    try { Stop-Transcript | Out-Null } catch { Write-Verbose 'transcript was not running' }
    exit 1
}

function Invoke-Wsl {
    # Runs wsl.exe with the given arguments; returns stdout lines; throws on non-zero exit.
    param([Parameter(Mandatory = $true)][string[]]$Arguments, [switch]$AllowFail)
    # PS 5.1: with 'Stop', a native stderr line redirected by 2>&1 becomes a terminating error.
    $ErrorActionPreference = 'Continue'
    $output = & wsl.exe @Arguments 2>&1
    $code = $LASTEXITCODE
    $text = ($output | ForEach-Object { "$_" }) -join "`n"
    $text = $text -replace "`0", ''
    if ($code -ne 0 -and -not $AllowFail) {
        throw "wsl $($Arguments -join ' ') failed (exit $code): $text"
    }
    return $text
}

function Invoke-InDistro {
    param([Parameter(Mandatory = $true)][string]$Command, [switch]$AllowFail)
    return Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'bash', '-lc', $Command) -AllowFail:$AllowFail
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-OsBuild {
    return [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuildNumber
}

function Test-Virtualization {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    if ($cs.HypervisorPresent) { return $true }
    $cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
    return [bool]$cpu.VirtualizationFirmwareEnabled
}

function Get-ResumeCommand {
    $argsList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Resume',
        '-Port', "$Port", '-BundlePath', "`"$BundlePath`"")
    if ($Offline) { $argsList += @('-Offline', "`"$Offline`"") }
    if ($AutoReboot) { $argsList += '-AutoReboot' }
    return "powershell.exe $($argsList -join ' ')"
}

function Enable-WslFeature {
    $needReboot = $false
    foreach ($feature in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')) {
        $state = Get-WindowsOptionalFeature -Online -FeatureName $feature
        if ($state.State -ne 'Enabled') {
            Write-Step "enabling Windows feature $feature"
            $result = Enable-WindowsOptionalFeature -Online -FeatureName $feature -All -NoRestart -WarningAction SilentlyContinue
            if ($result.RestartNeeded) { $needReboot = $true }
        }
        else {
            Write-Ok "$feature already enabled"
        }
    }
    return $needReboot
}

function Request-Reboot {
    # HKLM RunOnce runs elevated at the next administrator logon and continues the install.
    Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce' -Name $RunOnceName -Value (Get-ResumeCommand)
    Write-Host ''
    Write-Host 'Windows must restart to finish enabling WSL. Setup continues automatically after you sign in again.' -ForegroundColor Yellow
    $answer = 'y'
    if ($ProgressFile) {
        # the GUI installer offers the restart itself
        Write-Progress2 'reboot|'
        try { Stop-Transcript | Out-Null } catch { Write-Verbose 'transcript was not running' }
        exit 3010
    }
    if (-not $AutoReboot) { $answer = Read-Host 'Restart now? [Y/n]' }
    try { Stop-Transcript | Out-Null } catch { Write-Verbose 'transcript was not running' }
    if ($answer -eq '' -or $answer -match '^[YyДд]') { Restart-Computer -Force }
    exit 3010
}

function Test-DistroInstalled {
    $list = Invoke-Wsl -Arguments @('--list', '--quiet') -AllowFail
    return ($list -split "`n" | ForEach-Object { $_.Trim() }) -contains $Distro
}

function Install-Distro {
    Invoke-Wsl -Arguments @('--update') -AllowFail | Out-Null
    Invoke-Wsl -Arguments @('--set-default-version', '2') -AllowFail | Out-Null
    if (Test-DistroInstalled) {
        Write-Ok "$Distro already installed"
    }
    else {
        Write-Step "installing $Distro (no launch)"
        Invoke-Wsl -Arguments @('--install', '-d', $Distro, '--no-launch') | Out-Null
    }
    # Initialise the distro non-interactively: first try running as root directly (new tar-based
    # distros register on install); fall back to the classic launcher with --root.
    $probe = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'true') -AllowFail
    if ($LASTEXITCODE -ne 0) {
        $launcher = Get-Command $DistroLauncher -ErrorAction SilentlyContinue
        if (-not $launcher) { throw "$Distro is installed but not initialised and $DistroLauncher was not found: $probe" }
        & $launcher.Source install --root | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "$DistroLauncher install --root failed" }
    }
    Invoke-Wsl -Arguments @('--set-version', $Distro, '2') -AllowFail | Out-Null
    Write-Ok "$Distro ready"
}

function Initialize-Distro {
    Write-Step 'configuring the Linux user and systemd'
    $script = @"
set -e
id -u $LinuxUser >/dev/null 2>&1 || useradd --create-home --shell /bin/bash --groups sudo $LinuxUser
passwd -l $LinuxUser >/dev/null
echo '$LinuxUser ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-iler
chmod 440 /etc/sudoers.d/90-iler
cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[user]
default=$LinuxUser

[interop]
appendWindowsPath=false
EOF
"@
    Invoke-InDistro -Command ($script -replace "`r", '') | Out-Null
    Invoke-Wsl -Arguments @('--terminate', $Distro) -AllowFail | Out-Null
    Start-Sleep -Seconds 3
    $deadline = (Get-Date).AddSeconds(90)
    do {
        $pid1 = (Invoke-InDistro -Command 'ps -p 1 -o comm=' -AllowFail).Trim()
        if ($pid1 -eq 'systemd') { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($pid1 -ne 'systemd') { throw "systemd did not start inside $Distro (PID 1 is '$pid1')" }
    Write-Ok 'systemd is running inside WSL'
}

function Set-IniValue {
    # Minimal INI writer for .wslconfig: sets key=value inside [section], keeps everything else.
    param([string]$Path, [string]$Section, [string]$Key, [string]$Value)
    $lines = @()
    if (Test-Path $Path) { $lines = @(Get-Content -Path $Path) }
    $out = New-Object System.Collections.Generic.List[string]
    $inSection = $false; $sectionSeen = $false; $done = $false
    foreach ($line in $lines) {
        if ($line -match '^\s*\[(.+)\]\s*$') {
            if ($inSection -and -not $done) { $out.Add("$Key=$Value"); $done = $true }
            $inSection = ($Matches[1].Trim() -eq $Section)
            if ($inSection) { $sectionSeen = $true }
            $out.Add($line); continue
        }
        if ($inSection -and $line -match "^\s*$([regex]::Escape($Key))\s*=") {
            if (-not $done) { $out.Add("$Key=$Value"); $done = $true }
            continue
        }
        $out.Add($line)
    }
    if (-not $done) {
        if (-not $sectionSeen) { $out.Add("[$Section]") }
        $out.Add("$Key=$Value")
    }
    Set-Content -Path $Path -Value $out -Encoding ASCII
}

function Set-WslConfig([bool]$Mirrored) {
    # .wslconfig is per user: the distro and the boot task both run as the installing user.
    $path = Join-Path $env:USERPROFILE '.wslconfig'
    $totalMb = [math]::Floor((Get-CimInstance -ClassName Win32_ComputerSystem).TotalPhysicalMemory / 1MB)
    # Default WSL limit is 50% of RAM, too little on a 4 GB box: give WSL 75% (min 3 GB).
    $wslMb = [math]::Max(3072, [math]::Floor($totalMb * 0.75))
    if ($wslMb -gt $totalMb - 512) { $wslMb = $totalMb - 512 }
    Set-IniValue -Path $path -Section 'wsl2' -Key 'memory' -Value "$($wslMb)MB"
    Set-IniValue -Path $path -Section 'wsl2' -Key 'vmIdleTimeout' -Value '-1'
    if ($Mirrored) {
        Set-IniValue -Path $path -Section 'wsl2' -Key 'networkingMode' -Value 'mirrored'
    }
    Write-Ok ".wslconfig updated (memory $($wslMb)MB, mirrored=$Mirrored)"
}

function Copy-BundleToWsl {
    Write-Step 'copying the bundle into WSL'
    $winPath = (Resolve-Path $BundlePath).Path
    $linuxPath = (Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'wslpath', '-a', $winPath)).Trim()
    $cmd = "rm -rf '$WslBundleDir' && mkdir -p '$WslBundleDir' && cp -r '$linuxPath/compose.yml' '$linuxPath/deploy' '$WslBundleDir/' && find '$WslBundleDir' -name '*.sh' -exec sed -i 's/\r`$//' {} + && chmod +x '$WslBundleDir/deploy/install.sh'"
    Invoke-InDistro -Command $cmd | Out-Null
    Write-Ok "bundle copied to $WslBundleDir"
}

function Invoke-LinuxInstaller {
    Write-Step 'running deploy/install.sh inside WSL (Docker Engine, images, services)'
    $wslArgs = @('-d', $Distro, '-u', 'root', '--', 'bash', "$WslBundleDir/deploy/install.sh", '--port', "$Port", '--yes')
    if ($Offline) {
        $tarLinux = (Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'wslpath', '-a', (Resolve-Path $Offline).Path)).Trim()
        $wslArgs += @('--offline', $tarLinux)
    }
    # Stream the output (console + log) and map install.sh steps onto the GUI progress.
    & wsl.exe @wslArgs 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        if ($line -match 'installing Docker Engine') { Set-Stage 45 'docker' }
        elseif ($line -match 'loading images|pulling images') { Set-Stage 55 'images' }
        elseif ($line -match '==> starting services') { Set-Stage 80 'services' }
        elseif ($line -match 'waiting for services') { Set-Stage 85 'health' }
    }
    if ($LASTEXITCODE -ne 0) { throw "install.sh failed with exit code $LASTEXITCODE" }
}

function Save-PendingKeys {
    # Keep the technician's values in an admin/SYSTEM-only file: they must survive a WSL reboot.
    if (-not $KeysFile -or -not (Test-Path $KeysFile)) { return }
    Move-Item -Force -Path $KeysFile -Destination $PendingKeys
    & icacls.exe $PendingKeys /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
}

function Set-IlerKeys {
    # Applies the pending keys with deploy/set-llm-keys.sh --yes; values travel as environment variables
    # (WSLENV), never on a command line or in the log.
    if (-not (Test-Path $PendingKeys)) { return }
    $names = @()
    foreach ($line in Get-Content -Path $PendingKeys) {
        if ($line -match '^([A-Z_]+)=(.*)$' -and $KeyVars -contains $Matches[1] -and $Matches[2]) {
            Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
            $names += "$($Matches[1])/u"
        }
    }
    try {
        if ($names.Count -gt 0) {
            Write-Step 'applying ILER keys'
            $env:WSLENV = ($names -join ':')
            & wsl.exe -d $Distro -u root -- bash /opt/iler/deploy/set-llm-keys.sh --yes
            if ($LASTEXITCODE -ne 0) { throw "set-llm-keys.sh failed with exit code $LASTEXITCODE" }
        }
    }
    finally {
        foreach ($name in $KeyVars) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue }
        Remove-Item -Path Env:WSLENV -ErrorAction SilentlyContinue
        Remove-Item -Force -Path $PendingKeys -ErrorAction SilentlyContinue
    }
}

function Get-WslIp {
    $ip = (Invoke-InDistro -Command "hostname -I | awk '{print `$1}'" -AllowFail).Trim()
    return $ip
}

function Set-PortProxy {
    # Windows 10 / older Windows 11: forward LAN:Port to the (changing) WSL address, refreshed at boot.
    $scriptPath = Join-Path $DataDir 'refresh-portproxy.ps1'
    $refresh = @"
`$ErrorActionPreference = 'Continue'
`$env:WSL_UTF8 = '1'
for (`$i = 0; `$i -lt 40; `$i++) {
    `$ip = ((& wsl.exe -d $Distro -u root -- hostname -I) -join ' ').Trim().Split(' ')[0]
    if (`$ip) { break }
    Start-Sleep -Seconds 5
}
if (-not `$ip) { exit 1 }
& netsh.exe interface portproxy delete v4tov4 listenport=$Port listenaddress=0.0.0.0 | Out-Null
& netsh.exe interface portproxy add v4tov4 listenport=$Port listenaddress=0.0.0.0 connectport=$Port connectaddress=`$ip | Out-Null
"@
    Set-Content -Path $scriptPath -Value $refresh -Encoding UTF8
    Set-Service -Name iphlpsvc -StartupType Automatic
    Start-Service -Name iphlpsvc
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT45S'
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskPortProxy -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Ok "portproxy 0.0.0.0:$Port -> WSL (refreshed at boot by task '$TaskPortProxy')"
}

function Test-MirroredSupported {
    # networkingMode=mirrored needs Windows 11 22H2+ (build 22621) client editions.
    $isServer = (Get-CimInstance -ClassName Win32_OperatingSystem).ProductType -ne 1
    return ((-not $isServer) -and ((Get-OsBuild) -ge 22621))
}

function Set-Networking([bool]$Mirrored) {
    if ($Mirrored) {
        # Hyper-V firewall filters inbound traffic to WSL in mirrored mode. Allow the admin port only
        # from private (RFC1918 / ULA / link-local) and loopback sources, on Private/Domain networks.
        $ruleArgs = @{
            Name            = 'ILER-Admin'
            DisplayName     = $FirewallRule
            Direction       = 'Inbound'
            Action          = 'Allow'
            VMCreatorId     = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
            Protocol        = 'TCP'
            LocalPorts      = "$Port"
            RemoteAddresses = $PrivateRanges
        }
        try {
            Get-NetFirewallHyperVRule -Name 'ILER-Admin' -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule
            try {
                New-NetFirewallHyperVRule @ruleArgs -Profiles 'Domain, Private' -ErrorAction Stop | Out-Null
            }
            catch {
                # older builds of the Hyper-V firewall module have no -Profiles: keep the address filter
                Write-Warn "Hyper-V firewall rule without profile filter: $($_.Exception.Message)"
                New-NetFirewallHyperVRule @ruleArgs -ErrorAction Stop | Out-Null
            }
            Write-Ok "Hyper-V firewall: TCP $Port allowed only from private networks"
        }
        catch {
            Write-Warn "could not add Hyper-V firewall rule: $($_.Exception.Message)"
        }
        & netsh.exe interface portproxy delete v4tov4 listenport=$Port listenaddress=0.0.0.0 2>$null | Out-Null
        Unregister-ScheduledTask -TaskName $TaskPortProxy -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'WSL networking: mirrored'
    }
    else {
        Set-PortProxy
    }
}

function Set-Firewall {
    Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $FirewallRule -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $Port -Profile Private, Domain -RemoteAddress LocalSubnet | Out-Null
    Write-Ok "firewall: TCP $Port allowed from the local subnet (Private/Domain profiles)"
}

function Register-BootTask {
    # WSL distros are per user, so the task runs as the installing user (S4U: no stored password).
    # "sleep infinity" keeps the distro (and Docker inside it) alive without a logged-on session.
    $action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument "-d $Distro -u root -- sh -c `"exec sleep infinity`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskStart -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskStart
    Write-Ok "boot task '$TaskStart' registered"
}

function Get-SetupToken {
    # One-time token for the first-run wizard, generated by install.sh into /opt/iler/.env.
    $value = Invoke-InDistro -Command "sed -n 's/^ILER_SETUP_TOKEN=//p' /opt/iler/.env | head -n1" -AllowFail
    $value = "$value".Trim()
    if ($value -match '^[A-Za-z0-9_-]+$') { return $value }
    return ''
}

function Test-NeedsSetup {
    try {
        $state = Invoke-RestMethod -Uri "http://localhost:$Port/api/setup/state" -TimeoutSec 10
        return [bool]$state.needs_setup
    }
    catch {
        return $true  # unknown: keep the setup link (it is harmless once the owner exists)
    }
}

function New-AdminShortcut([string]$Path = '/') {
    # Until the owner is created the shortcut opens the setup link (with the one-time token).
    $desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
    # PowerShell variables are case-insensitive: do not name this $path (it would overwrite $Path).
    $shortcutFile = Join-Path $desktop $ShortcutName
    $content = "[InternetShortcut]`r`nURL=http://localhost:$Port$Path`r`n"
    [IO.File]::WriteAllText($shortcutFile, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok "desktop shortcut: $shortcutFile"
}

function Get-LanAddress {
    $addr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' -and $_.InterfaceAlias -notmatch 'WSL|vEthernet|Loopback' -and $_.AddressState -eq 'Preferred' } |
        Select-Object -First 1
    if ($addr) { return $addr.IPAddress }
    return ''
}

function Wait-AdminUi {
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:$Port/" -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) { return $true }
        }
        catch { Start-Sleep -Seconds 3 }
    }
    return $false
}

# ---- main ---------------------------------------------------------------------------------

if (-not (Test-Admin)) {
    Write-Host 'Run this installer as Administrator.' -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Start-Transcript -Path $LogFile -Append | Out-Null

if (-not $BundlePath) { $BundlePath = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
if (-not (Test-Path (Join-Path $BundlePath 'compose.yml'))) { Stop-Install "compose.yml not found in bundle folder '$BundlePath'" }
if ($Offline -and -not (Test-Path $Offline)) { Stop-Install "offline archive not found: $Offline" }
Save-PendingKeys
if ((-not $Offline) -and (Test-Path (Join-Path $BundlePath 'images.tar'))) { $Offline = Join-Path $BundlePath 'images.tar' }

try {
    Set-Stage 3 'check'
    $build = Get-OsBuild
    Write-Step "ILER Bot setup (Windows build $build, port $Port, resume=$Resume)"
    if ($build -lt 19045 -and $build -ne 20348) {
        # 19045 = Windows 10 22H2, 20348 = Windows Server 2022; Server 2019 has no WSL2.
        Stop-Install "unsupported Windows build $build (need Windows 10 22H2, Windows 11 or Server 2022+)"
    }
    if (-not (Test-Virtualization)) {
        Stop-Install 'hardware virtualization is disabled; enable Intel VT-x / AMD-V (SVM) in BIOS/UEFI and run setup again'
    }
    Write-Ok 'virtualization available'
    if ($build -lt 22000 -and $build -ne 20348) {
        # Windows 10 22H2: supported, but WSL mirrored networking is unavailable (portproxy fallback).
        Write-Warn 'Windows 10 detected: supported, but Windows 11 is recommended (more reliable WSL networking).'
    }

    Set-Stage 8 'wsl'
    if (Enable-WslFeature) { Request-Reboot }
    Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce' -Name $RunOnceName -ErrorAction SilentlyContinue

    Set-Stage 12 'distro'
    Install-Distro
    $mirrored = Test-MirroredSupported
    Set-WslConfig -Mirrored $mirrored
    # .wslconfig changes apply only after the whole WSL VM restarts
    Invoke-Wsl -Arguments @('--shutdown') -AllowFail | Out-Null
    Set-Stage 30 'systemd'
    Initialize-Distro
    Set-Stage 38 'copy'
    Copy-BundleToWsl
    Set-Stage 42 'linux'
    Invoke-LinuxInstaller
    Set-Stage 90 'keys'
    Set-IlerKeys
    Set-Stage 92 'network'
    Set-Networking -Mirrored $mirrored
    Set-Firewall
    Register-BootTask

    Set-Stage 96 'finish'
    if (-not (Wait-AdminUi)) { Write-Warn "admin UI did not answer on http://localhost:$Port/ yet; give it a minute" }
    $setupPath = ''
    $token = Get-SetupToken
    if ($token -and (Test-NeedsSetup)) { $setupPath = "/setup?token=$token" }
    if ($setupPath) { New-AdminShortcut -Path $setupPath } else { New-AdminShortcut }
    $lan = Get-LanAddress
    Write-Host ''
    Write-Host 'ILER Bot is installed. Open the admin UI:' -ForegroundColor Green
    if ($setupPath) {
        Write-Host 'First run: create the owner account with this one-time setup link (keep it private):' -ForegroundColor Yellow
        Write-Host "  http://localhost:$Port$setupPath"
        if ($lan) { Write-Host "  http://$($lan):$Port$setupPath   (from another computer in the local network)" }
        Write-Host 'The desktop shortcut opens this link until setup is done.'
    }
    else {
        Write-Host "  http://localhost:$Port"
        if ($lan) { Write-Host "  http://$($lan):$Port   (from other computers in the local network)" }
    }
    Write-Host "Log: $LogFile"
    Write-Progress2 "done|http://localhost:$Port$setupPath"
}
catch {
    Stop-Install $_.Exception.Message
}
finally {
    try { Stop-Transcript | Out-Null } catch { Write-Verbose 'transcript was not running' }
}
exit 0
