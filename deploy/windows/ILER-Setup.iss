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
; install.ps1 handles the WSL reboot itself (scheduled resume); never let Setup reboot on its own
RestartIfNeededByRun=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#BundleRoot}\compose.yml"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleRoot}\deploy\*"; DestDir: "{app}\deploy"; Flags: ignoreversion recursesubdirs createallsubdirs
; offline bundle only (docker save archive); skipped when absent
Source: "{#BundleRoot}\images.tar"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist nocompression

[Run]
; install.ps1 itself runs hidden from [Code] (RunInstaller) with a progress bar; this only opens the admin UI.
Filename: "{code:GetResultUrl}"; Description: "{cm:OpenAdmin}"; Flags: postinstall shellexec nowait skipifsilent; Check: InstallSucceeded

[CustomMessages]
ru.ProgressCaption=Установка ILER Bot
ru.ProgressDesc=Настраиваем WSL, Docker и сервисы бота. Это займёт 10–15 минут, не выключайте компьютер.
ru.OpenAdmin=Открыть админку ILER Bot
ru.St_check=Проверка системы…
ru.St_wsl=Включение компонентов WSL…
ru.St_distro=Установка Ubuntu 24.04…
ru.St_systemd=Настройка Linux…
ru.St_copy=Копирование файлов…
ru.St_linux=Подготовка установки…
ru.St_docker=Установка Docker Engine…
ru.St_images=Загрузка образов бота…
ru.St_services=Запуск сервисов…
ru.St_health=Ожидание готовности сервисов…
ru.St_network=Настройка сети и брандмауэра…
ru.St_finish=Завершение…
ru.Starting=Запуск установки…
ru.DoneHeading=ILER Bot установлен
ru.DoneText=Бот запущен. Откройте админку, чтобы создать владельца и подключить WhatsApp:%n%n%1%n%nЯрлык «ILER Bot - админка» на рабочем столе открывает эту ссылку.
ru.FailHeading=Установка не завершена
ru.FailText=Ошибка: %1%n%nПодробный журнал: %2%nОтправьте его в поддержку или запустите установщик ещё раз.
ru.RebootText=Для включения WSL нужна перезагрузка. После входа в систему установка продолжится автоматически.
en.ProgressCaption=Installing ILER Bot
en.ProgressDesc=Setting up WSL, Docker and the bot services. This takes 10-15 minutes, keep the computer on.
en.OpenAdmin=Open the ILER Bot admin UI
en.St_check=Checking the system...
en.St_wsl=Enabling WSL features...
en.St_distro=Installing Ubuntu 24.04...
en.St_systemd=Configuring Linux...
en.St_copy=Copying files...
en.St_linux=Preparing installation...
en.St_docker=Installing Docker Engine...
en.St_images=Loading bot images...
en.St_services=Starting services...
en.St_health=Waiting for services...
en.St_network=Configuring network and firewall...
en.St_finish=Finishing...
en.Starting=Starting installation...
en.DoneHeading=ILER Bot is installed
en.DoneText=The bot is running. Open the admin UI to create the owner and connect WhatsApp:%n%n%1%n%nThe desktop shortcut opens this link.
en.FailHeading=Installation did not finish
en.FailText=Error: %1%n%nFull log: %2%nSend it to support or run the installer again.
en.RebootText=Windows must restart to enable WSL. Setup continues automatically after you sign in.

[UninstallRun]
Filename: "{sys}\wsl.exe"; Parameters: "-d Ubuntu-24.04 -u root -- bash /opt/iler/deploy/install.sh --uninstall --yes"; \
  Flags: runhidden waituntilterminated; RunOnceId: "IlerUninstallStack"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ILER Bot - start WSL"" /F"; Flags: runhidden; RunOnceId: "IlerDelTaskStart"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ILER Bot - refresh portproxy"" /F"; Flags: runhidden; RunOnceId: "IlerDelTaskProxy"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""ILER Bot admin UI"""; Flags: runhidden; RunOnceId: "IlerDelFirewall"

[UninstallDelete]
Type: files; Name: "{commondesktop}\ILER Bot - *.url"

[Code]
var
  ResultUrl, ErrorText: String;
  NeedReboot, InstallFailed, InstallerRan: Boolean;

function InstallSucceeded: Boolean;
begin
  Result := InstallerRan and (not InstallFailed) and (not NeedReboot) and (ResultUrl <> '');
end;

function GetResultUrl(Param: String): String;
begin
  Result := ResultUrl;
end;

function StageText(Stage: String): String;
begin
  if Stage = 'check' then Result := CustomMessage('St_check')
  else if Stage = 'wsl' then Result := CustomMessage('St_wsl')
  else if Stage = 'distro' then Result := CustomMessage('St_distro')
  else if Stage = 'systemd' then Result := CustomMessage('St_systemd')
  else if Stage = 'copy' then Result := CustomMessage('St_copy')
  else if Stage = 'linux' then Result := CustomMessage('St_linux')
  else if Stage = 'docker' then Result := CustomMessage('St_docker')
  else if Stage = 'images' then Result := CustomMessage('St_images')
  else if Stage = 'services' then Result := CustomMessage('St_services')
  else if Stage = 'health' then Result := CustomMessage('St_health')
  else if Stage = 'network' then Result := CustomMessage('St_network')
  else if Stage = 'finish' then Result := CustomMessage('St_finish')
  else Result := '';
end;

function LastLine(const FileName: String): String;
var
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := '';
  if not FileExists(FileName) then Exit;
  if LoadStringsFromFile(FileName, Lines) then
    for I := GetArrayLength(Lines) - 1 downto 0 do
      if Trim(Lines[I]) <> '' then
      begin
        Result := Trim(Lines[I]);
        Exit;
      end;
end;

// install.ps1 runs hidden and appends "percent|stage|" lines to the progress file; the last line
// becomes "done|<url>", "error|<message>" or "reboot|". Between stages the bar creeps forward slowly.
procedure RunInstaller;
var
  Page: TOutputProgressWizardPage;
  ResultCode, P, Pct, Shown, Ticks: Integer;
  ProgressFile, Params, Line, Kind, Rest, Stage, Text: String;
  Finished: Boolean;
begin
  InstallerRan := True;
  ProgressFile := ExpandConstant('{tmp}\progress.txt');
  DeleteFile(ProgressFile);
  Params := '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    ExpandConstant('{app}\deploy\windows\install.ps1') + '" -BundlePath "' + ExpandConstant('{app}') +
    '" -ProgressFile "' + ProgressFile + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE, ewNoWait, ResultCode) then
  begin
    InstallFailed := True;
    ErrorText := SysErrorMessage(ResultCode);
    Exit;
  end;
  Page := CreateOutputProgressPage(CustomMessage('ProgressCaption'), CustomMessage('ProgressDesc'));
  Page.SetText(CustomMessage('Starting'), '');
  Page.SetProgress(0, 100);
  Page.Show;
  try
    Pct := 0; Shown := 0; Ticks := 0; Finished := False;
    while not Finished do
    begin
      Sleep(500);
      Ticks := Ticks + 1;
      Line := LastLine(ProgressFile);
      if Line <> '' then
      begin
        P := Pos('|', Line);
        Kind := Copy(Line, 1, P - 1);
        Rest := Copy(Line, P + 1, Length(Line));
        if Kind = 'done' then begin ResultUrl := Rest; Finished := True; end
        else if Kind = 'error' then begin InstallFailed := True; ErrorText := Rest; Finished := True; end
        else if Kind = 'reboot' then begin NeedReboot := True; Finished := True; end
        else
        begin
          Pct := StrToIntDef(Kind, Pct);
          P := Pos('|', Rest);
          if P > 0 then Stage := Copy(Rest, 1, P - 1) else Stage := Rest;
          Text := StageText(Stage);
          if Text <> '' then Page.SetText(Text, '');
        end;
      end;
      if Shown < Pct then Shown := Pct
      else if (Shown < Pct + 8) and (Ticks mod 10 = 0) then Shown := Shown + 1;
      if Shown > 99 then Shown := 99;
      Page.SetProgress(Shown, 100);
      // 90 minutes without a final status: the script died or hangs
      if Ticks > 2 * 60 * 90 then
      begin
        InstallFailed := True;
        ErrorText := 'timeout (no response from install.ps1)';
        Finished := True;
      end;
    end;
    Page.SetProgress(100, 100);
  finally
    Page.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then RunInstaller;
end;

function NeedRestart: Boolean;
begin
  Result := NeedReboot;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID <> wpFinished then Exit;
  if InstallFailed then
  begin
    WizardForm.FinishedHeadingLabel.Caption := CustomMessage('FailHeading');
    WizardForm.FinishedLabel.Caption := FmtMessage(CustomMessage('FailText'), [ErrorText, ExpandConstant('{commonappdata}\ILER\install.log')]);
  end
  else if NeedReboot then
    WizardForm.FinishedLabel.Caption := CustomMessage('RebootText')
  else if ResultUrl <> '' then
  begin
    WizardForm.FinishedHeadingLabel.Caption := CustomMessage('DoneHeading');
    WizardForm.FinishedLabel.Caption := FmtMessage(CustomMessage('DoneText'), [ResultUrl]);
  end;
end;
