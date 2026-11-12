<#
.SYNOPSIS
    ILER Bot installer for Windows (WSL2 + Ubuntu 24.04 + Docker Engine inside WSL).

.DESCRIPTION
    Spec section 8 (Windows). Supported: Windows 10 22H2, Windows 11, Windows Server 2022+.
    Steps: admin/virtualization checks -> enable WSL + VirtualMachinePlatform (elevated one-shot logon
    task resumes after the reboot) -> install Ubuntu-24.04 without interaction -> systemd on -> copy the bundle into WSL and
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
    Internal: set by the one-shot logon task ('ILER Bot - resume setup') after a reboot; the window
    stays open at the end so the setup link or the error can be read.
.PARAMETER DistroDir
    Folder for the Ubuntu-24.04 disk (ext4.vhdx: database, images, backups) when the distro is installed
    by this script; default: WSL's own location in the user profile. An existing distro is not moved.
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
    [string]$KeysFile = '',
    [string]$DistroDir = ''
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
$TaskResume = 'ILER Bot - resume setup'
$RunOnceName = 'ILERSetupResume'  # legacy (RunOnce does not elevate); removed if still present
# visible run after a reboot (no GUI installer reading the progress file): keep the window open
$Interactive = [bool]($Resume -and -not $ProgressFile)
$InstallUser = "$($env:USERDOMAIN)\$($env:USERNAME)"
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
    # Inno's LoadStringsFromFile reads a BOM-less file as ANSI (Russian error text would garble): the
    # first write creates the file with a UTF-8 BOM, later appends add no second BOM.
    if (-not $ProgressFile) { return }
    try {
        $isNew = (-not (Test-Path -LiteralPath $ProgressFile)) -or ((Get-Item -LiteralPath $ProgressFile).Length -eq 0)
        [IO.File]::AppendAllText($ProgressFile, "$Line`r`n", (New-Object System.Text.UTF8Encoding($isNew)))
    }
    catch { Write-Verbose "progress file: $($_.Exception.Message)" }
}
function Set-Stage([int]$Percent, [string]$Stage) { Write-Progress2 "$Percent|$Stage|" }

function Wait-Close {
    # Resume run in a visible window: keep it open so the setup link / error can be read.
    if (-not $Interactive) { return }
    try { Read-Host 'Нажмите Enter, чтобы закрыть окно' | Out-Null } catch { Write-Verbose 'no console input' }
}

function Stop-Install([string]$Message) {
    Write-Progress2 ("error|" + ($Message -replace '[\r\n|]+', ' '))
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host "Log: $LogFile"
    try { Stop-Transcript | Out-Null } catch { Write-Verbose 'transcript was not running' }
    Wait-Close
    exit 1
}

function Set-PrivateAcl {
    # Folder readable only by Administrators, SYSTEM and the installing user (inheritance removed): the
    # install log holds the one-time setup token, pending-keys.env the LLM keys, wsl\ the database disk.
    param([string]$Dir, [switch]$ResetChildren)
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & icacls.exe $Dir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "$($InstallUser):(OI)(CI)F" /Q | Out-Null
        $aclCode = $LASTEXITCODE
        if ($aclCode -ne 0) {
            # the user name may not resolve (e.g. run as SYSTEM by a deployment tool): admins + SYSTEM only
            & icacls.exe $Dir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' /Q | Out-Null
            $aclCode = $LASTEXITCODE
        }
        if ($ResetChildren -and $aclCode -eq 0 -and (Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue)) {
            # existing files (log from an earlier run, an older distro disk): inherit the new ACL
            & icacls.exe (Join-Path $Dir '*') /reset /T /C /Q | Out-Null
        }
    }
    finally {
        $ErrorActionPreference = $prev
    }
    if ($aclCode -ne 0) { throw "could not restrict access to $Dir (icacls exit $aclCode)" }
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

function ConvertTo-QuotedArg([string]$Value) {
    # "..." for the Windows command line; a trailing backslash is doubled so it does not escape the quote.
    if ($Value.EndsWith('\')) { $Value = $Value + '\' }
    return "`"$Value`""
}

function Get-ResumeCommand {
    # Returns @(<exe>, <argument string>) for the resume task.
    $exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $argsList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (ConvertTo-QuotedArg $PSCommandPath), '-Resume',
        '-Port', "$Port", '-BundlePath', (ConvertTo-QuotedArg $BundlePath))
    if ($Offline) { $argsList += @('-Offline', (ConvertTo-QuotedArg $Offline)) }
    if ($AutoReboot) { $argsList += '-AutoReboot' }
    if ($DistroDir) { $argsList += @('-DistroDir', (ConvertTo-QuotedArg $DistroDir)) }
    return @($exe, ($argsList -join ' '))
}

function Remove-ResumeTask {
    # one-shot: the resumed run (or any later run) removes the task and the legacy RunOnce value
    try {
        if (Get-ScheduledTask -TaskName $TaskResume -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskResume -Confirm:$false -ErrorAction Stop
        }
        Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce' -Name $RunOnceName -ErrorAction SilentlyContinue
    }
    catch { Write-Warn "could not remove the resume task: $($_.Exception.Message)" }
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
    # RunOnce does not elevate, so the resume would fail the admin check: use a one-shot scheduled task
    # that runs elevated (Highest) and visible (Interactive) at the installing user's next logon.
    # The resumed run unregisters it first thing.
    $resume = Get-ResumeCommand
    $action = New-ScheduledTaskAction -Execute $resume[0] -Argument $resume[1]
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $InstallUser
    $principal = New-ScheduledTaskPrincipal -UserId $InstallUser -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    Register-ScheduledTask -TaskName $TaskResume -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Ok "setup resumes after the reboot (task '$TaskResume')"
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
        if ($DistroDir) {
            Write-Step "installing $Distro into $DistroDir (no launch)"
            New-Item -ItemType Directory -Force -Path $DistroDir | Out-Null
            Invoke-Wsl -Arguments @('--install', '-d', $Distro, '--no-launch', '--location', $DistroDir) -AllowFail | Out-Null
            if (-not (Test-DistroInstalled)) {
                # older WSL without --location: default place in the user profile
                Write-Warn "WSL cannot install into $DistroDir (update WSL: wsl --update); using the default location"
                Invoke-Wsl -Arguments @('--install', '-d', $Distro, '--no-launch') | Out-Null
            }
        }
        else {
            Write-Step "installing $Distro (no launch)"
            Invoke-Wsl -Arguments @('--install', '-d', $Distro, '--no-launch') | Out-Null
        }
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
    # Returns $true when the file content changed (and was rewritten).
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
    if ((@($lines) -join "`n") -ceq (@($out) -join "`n")) { return $false }
    Set-Content -Path $Path -Value $out -Encoding ASCII
    return $true
}

function Get-IniValue {
    param([string]$Path, [string]$Section, [string]$Key)
    if (-not (Test-Path $Path)) { return '' }
    $inSection = $false
    foreach ($line in @(Get-Content -Path $Path)) {
        if ($line -match '^\s*\[(.+)\]\s*$') { $inSection = ($Matches[1].Trim() -eq $Section); continue }
        if ($inSection -and $line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*?)\s*$") { return $Matches[1] }
    }
    return ''
}

function ConvertTo-Megabytes([string]$Size) {
    # .wslconfig sizes: 8GB, 4096MB, 1.5GB ... -> MB; 0 when unparsable
    if ($Size -notmatch '^\s*(\d+(?:\.\d+)?)\s*([KMGT]?)B?\s*$') { return 0 }
    $n = [double]$Matches[1]
    switch ($Matches[2].ToUpper()) {
        'T' { return [math]::Floor($n * 1024 * 1024) }
        'G' { return [math]::Floor($n * 1024) }
        'M' { return [math]::Floor($n) }
        'K' { return [math]::Floor($n / 1024) }
        default { return [math]::Floor($n / 1MB) }
    }
}

function Set-WslConfig([bool]$Mirrored) {
    # .wslconfig is per user: the distro and the boot task both run as the installing user.
    # Returns $true when the file changed (the WSL VM must then restart to apply it).
    $iniPath = Join-Path $env:USERPROFILE '.wslconfig'
    $totalMb = [math]::Floor((Get-CimInstance -ClassName Win32_ComputerSystem).TotalPhysicalMemory / 1MB)
    # Default WSL limit is 50% of RAM, too little on a 4 GB box: give WSL 75% (min 3 GB).
    $wslMb = [math]::Max(3072, [math]::Floor($totalMb * 0.75))
    if ($wslMb -gt $totalMb - 512) { $wslMb = $totalMb - 512 }
    $changed = $false
    # only raise the limit: a larger value set by the user (e.g. for Docker Desktop) is kept
    $currentMb = ConvertTo-Megabytes (Get-IniValue -Path $iniPath -Section 'wsl2' -Key 'memory')
    if ($currentMb -ge $wslMb) {
        Write-Ok ".wslconfig memory limit kept ($($currentMb)MB >= $($wslMb)MB)"
        $wslMb = $currentMb
    }
    elseif (Set-IniValue -Path $iniPath -Section 'wsl2' -Key 'memory' -Value "$($wslMb)MB") { $changed = $true }
    if (Set-IniValue -Path $iniPath -Section 'wsl2' -Key 'vmIdleTimeout' -Value '-1') { $changed = $true }
    if ($Mirrored) {
        if (Set-IniValue -Path $iniPath -Section 'wsl2' -Key 'networkingMode' -Value 'mirrored') { $changed = $true }
    }
    if ($changed) { Write-Ok ".wslconfig updated (memory $($wslMb)MB, mirrored=$Mirrored)" }
    else { Write-Ok '.wslconfig already up to date' }
    return $changed
}

function ConvertTo-WslArg([string]$WinPath) {
    # wsl.exe runs the command through the distro's shell, which eats backslashes in an unquoted
    # argument (D:\ILER -> D:ILER). wslpath accepts forward slashes, so pass D:/ILER instead.
    return ($WinPath -replace '\\', '/')
}

function Copy-BundleToWsl {
    Write-Step 'copying the bundle into WSL'
    $winPath = (Resolve-Path $BundlePath).Path
    $linuxPath = (Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'wslpath', '-a', (ConvertTo-WslArg $winPath))).Trim()
    if (-not $linuxPath.StartsWith('/') -or $linuxPath -match "[\r\n]") { throw "unexpected WSL path for '$winPath': '$linuxPath'" }
    # embedded in single quotes below: close, escaped quote, reopen
    $linuxPath = $linuxPath -replace "'", "'\''"
    $cmd = "rm -rf '$WslBundleDir' && mkdir -p '$WslBundleDir' && cp -r '$linuxPath/compose.yml' '$linuxPath/deploy' '$WslBundleDir/' && find '$WslBundleDir' -name '*.sh' -exec sed -i 's/\r`$//' {} + && chmod +x '$WslBundleDir/deploy/install.sh'"
    Invoke-InDistro -Command $cmd | Out-Null
    Write-Ok "bundle copied to $WslBundleDir"
}

function Invoke-LinuxInstaller {
    Write-Step 'running deploy/install.sh inside WSL (Docker Engine, images, services)'
    $wslArgs = @('-d', $Distro, '-u', 'root', '--', 'bash', "$WslBundleDir/deploy/install.sh", '--port', "$Port", '--yes')
    if ($Offline) {
        $tarLinux = (Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'wslpath', '-a', (ConvertTo-WslArg (Resolve-Path $Offline).Path))).Trim()
        $wslArgs += @('--offline', $tarLinux)
    }
    # Stream the output (console + log) and map install.sh steps onto the GUI progress.
    # Windows PowerShell 5.1 turns every stderr line of a native command into an ErrorRecord when it is
    # redirected (2>&1); with ErrorActionPreference=Stop that aborts the install on a harmless warning
    # (get.docker.com prints "WSL DETECTED" to stderr). Only the exit code decides success here.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & wsl.exe @wslArgs 2>&1 | ForEach-Object {
            $line = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
            Write-Host $line
            if ($line -match 'installing Docker Engine') { Set-Stage 45 'docker' }
            elseif ($line -match 'loading images|pulling images') { Set-Stage 55 'images' }
            elseif ($line -match '==> starting services') { Set-Stage 80 'services' }
            elseif ($line -match 'waiting for services') { Set-Stage 85 'health' }
        }
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) { throw "install.sh failed with exit code $code (see the lines above in the log)" }
}

function Save-PendingKeys {
    # Keep the technician's values in an admin/SYSTEM-only file: they must survive a WSL reboot.
    # Written as a new file (not moved: a move keeps the ACL of the temp folder), so it inherits the
    # restricted ACL of $DataDir; then narrowed to Administrators/SYSTEM.
    if (-not $KeysFile -or -not (Test-Path $KeysFile)) { return }
    $keysText = [IO.File]::ReadAllText($KeysFile)
    Remove-Item -Force -Path $PendingKeys -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText($PendingKeys, $keysText, (New-Object System.Text.UTF8Encoding($false)))
    Remove-Item -Force -Path $KeysFile
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
    # only a plain IPv4 address goes into the netsh command line
    if (`$ip -match '^\d{1,3}(\.\d{1,3}){3}`$') { break }
    `$ip = ''
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
        # 127.0.0.1, not localhost: under WSL mirrored mode "localhost" may resolve to ::1 and hang
        $state = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/setup/state" -TimeoutSec 10
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
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) { return $true }
        }
        catch { Start-Sleep -Seconds 3 }
    }
    return $false
}

function Test-PortFree {
    # The admin port must be free on Windows, or held by our own WSL stack from an earlier run
    # (wslrelay/wslhost; on Windows 10 the iphlpsvc portproxy inside svchost).
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { Write-Ok "port $Port is free"; return }
    $proxyOwned = $false
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $proxy = (& netsh.exe interface portproxy show v4tov4 | Out-String)
        $proxyOwned = $proxy -match "(?m)^\s*0\.0\.0\.0\s+$Port\s"
    }
    finally { $ErrorActionPreference = $prev }
    foreach ($conn in $listeners) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { "PID $($conn.OwningProcess)" }
        if ($name -in @('wslrelay', 'wslhost')) { continue }
        if ($name -eq 'svchost' -and $proxyOwned) { continue }
        if ($name -in @('com.docker.backend', 'com.docker.proxy', 'vpnkit')) {
            Stop-Install "port $Port is used by Docker Desktop ($name): stop the Docker Desktop container that publishes port $Port (or quit Docker Desktop) and run setup again, or reinstall with another port"
        }
        Stop-Install "port $Port is used by $name; free it or reinstall with another port"
    }
    Write-Ok "port $Port is held by the existing ILER WSL stack"
}

function Test-FreeSpace {
    # The Ubuntu disk (database, images, backups) grows over time.
    $target = $DistroDir
    if (-not $target) { $target = $env:SystemDrive + '\' }
    $drive = ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($target))).TrimEnd('\')
    $disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$drive'" -ErrorAction SilentlyContinue
    if (-not $disk -or $null -eq $disk.FreeSpace) { Write-Warn "cannot read free space of drive $drive"; return }
    $freeGb = [math]::Round($disk.FreeSpace / 1GB, 1)
    if ($freeGb -lt 20) {
        if (Test-DistroInstalled) {
            # re-run: the distro disk already exists and holds most of the data
            Write-Warn "only $freeGb GB free on $drive; at least 20 GB is recommended"
        }
        else {
            Stop-Install "only $freeGb GB free on drive $drive; at least 20 GB is required (40 GB recommended). Free up space or choose another drive."
        }
    }
    elseif ($freeGb -lt 40) { Write-Warn "only $freeGb GB free on drive $drive; 40 GB or more is recommended" }
    else { Write-Ok "$freeGb GB free on drive $drive" }
}

function Test-PublicNetwork {
    # The firewall rules apply to Private/Domain networks only. Do not change the category ourselves.
    try {
        $public = @(Get-NetConnectionProfile -ErrorAction Stop | Where-Object { "$($_.NetworkCategory)" -eq 'Public' })
    }
    catch { return }
    foreach ($netProfile in $public) {
        Write-Warn ("network '$($netProfile.Name)' ($($netProfile.InterfaceAlias)) is Public: other computers cannot open the admin UI " +
            "until it is set to Private. As administrator run: Set-NetConnectionProfile -InterfaceIndex $($netProfile.InterfaceIndex) -NetworkCategory Private")
    }
}

# ---- main ---------------------------------------------------------------------------------

if (-not (Test-Admin)) {
    Write-Progress2 'error|install.ps1 must run as Administrator'
    Write-Host 'Run this installer as Administrator.' -ForegroundColor Red
    Wait-Close
    exit 1
}
# Restrict ProgramData\ILER before anything is written there (log with the setup token, pending keys).
try { Set-PrivateAcl -Dir $DataDir -ResetChildren }
catch { Stop-Install "$($_.Exception.Message)" }
Start-Transcript -Path $LogFile -Append | Out-Null
Set-Stage 3 'check'
Remove-ResumeTask

if (-not $BundlePath) { $BundlePath = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
if (-not (Test-Path (Join-Path $BundlePath 'compose.yml'))) { Stop-Install "compose.yml not found in bundle folder '$BundlePath'" }
if ($Offline -and -not (Test-Path $Offline)) { Stop-Install "offline archive not found: $Offline" }
if ((-not $Offline) -and (Test-Path (Join-Path $BundlePath 'images.tar'))) { $Offline = Join-Path $BundlePath 'images.tar' }

try {
    # wslpath / wsl output is UTF-8 (WSL_UTF8=1): decode it as such (non-Latin bundle paths)
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false }
    catch { Write-Verbose "console encoding: $($_.Exception.Message)" }
    Save-PendingKeys
    # The distro disk folder: inherited entries of an existing ext4.vhdx follow the new folder ACL; its
    # explicit entries (WSL grants the VM access on attach) are left alone, so no /reset here.
    if ($DistroDir) { Set-PrivateAcl -Dir $DistroDir }
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

    Test-PortFree
    Test-FreeSpace

    Set-Stage 12 'distro'
    Install-Distro
    $mirrored = Test-MirroredSupported
    # .wslconfig changes apply only after the whole WSL VM restarts; shut it down only when something
    # changed (it also stops Docker Desktop and any other distro the user runs)
    if (Set-WslConfig -Mirrored $mirrored) { Invoke-Wsl -Arguments @('--shutdown') -AllowFail | Out-Null }
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
    Test-PublicNetwork
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
    Wait-Close
}
catch {
    $msg = "$($_.Exception.Message)".Trim()
    if (-not $msg) { $msg = "$($_.Exception.GetType().Name) at line $($_.InvocationInfo.ScriptLineNumber)" }
    Stop-Install $msg
}
finally {
    try { Stop-Transcript | Out-Null } catch { Write-Verbose 'transcript was not running' }
}
exit 0
