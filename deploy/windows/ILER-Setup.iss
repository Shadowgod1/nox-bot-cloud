; ILER Bot - Windows installer (Inno Setup 6).
; Build from the repository root or an unpacked release bundle:
;   iscc /DAppVersion=0.1.0 deploy\windows\ILER-Setup.iss
; Output: dist\ILER-Setup-<version>.exe
; The installer copies the bundle (compose.yml, deploy\, optional images.tar) to {app}
; and runs deploy\windows\install.ps1, which does the real work (WSL2, Ubuntu, Docker, services).

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define BundleRoot "..\.."

[Setup]
AppId={{6F1C3E2A-9B7D-4C1E-A5F2-1D0B7C9E4A11}
AppName=ILER Bot
AppVersion={#AppVersion}
AppPublisher=ILER
DefaultDirName={commonpf}\ILER Bot
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir={#BundleRoot}\dist
OutputBaseFilename=ILER-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
#ifdef Span
; split output: small exe + ILER-Setup-<ver>-N.bin volumes (for transfer limits); keep all in one folder
DiskSpanning=yes
DiskSliceSize=419430400
#endif
WizardStyle=modern
UninstallDisplayName=ILER Bot
CloseApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#BundleRoot}\compose.yml"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleRoot}\deploy\*"; DestDir: "{app}\deploy"; Flags: ignoreversion recursesubdirs createallsubdirs
; offline bundle only (docker save archive); skipped when absent
Source: "{#BundleRoot}\images.tar"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist nocompression

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\deploy\windows\install.ps1"" -BundlePath ""{app}"""; \
  StatusMsg: "Installing ILER Bot (WSL2, Docker, services). This can take 10-15 minutes..."; \
  Flags: waituntilterminated

[UninstallRun]
Filename: "{sys}\wsl.exe"; Parameters: "-d Ubuntu-24.04 -u root -- bash /opt/iler/deploy/install.sh --uninstall --yes"; \
  Flags: runhidden waituntilterminated; RunOnceId: "IlerUninstallStack"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ILER Bot - start WSL"" /F"; Flags: runhidden; RunOnceId: "IlerDelTaskStart"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ILER Bot - refresh portproxy"" /F"; Flags: runhidden; RunOnceId: "IlerDelTaskProxy"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""ILER Bot admin UI"""; Flags: runhidden; RunOnceId: "IlerDelFirewall"

[UninstallDelete]
Type: files; Name: "{commondesktop}\ILER Bot - *.url"
