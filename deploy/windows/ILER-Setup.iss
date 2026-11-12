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
; everything lives in the chosen folder: the bundle and wsl\ (the Ubuntu disk with the database, images
; and backups, which grows over time) - pick a drive with space, e.g. D:\ILER
DefaultDirName={sd}\ILER
DisableDirPage=no
UsePreviousAppDir=yes
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
Filename: "{code:GetResultUrl}"; Description: "{cm:OpenAdmin}"; Flags: postinstall shellexec nowait skipifsilent runasoriginaluser; Check: InstallSucceeded

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
ru.St_keys=Применение ключей ILER…
ru.ChangeKeys=Сменить ключи ILER (для техника)
ru.KeysCaption=Ключи ILER
ru.KeysDesc=Заполняет техник ILER
ru.KeysIntro=Ключи доступа ILER к языковым моделям и месячный лимит коинов клиента. Они хранятся только на этом компьютере и не показываются в админке. Пустое поле — без этого провайдера. Позже их можно сменить ярлыком «Сменить ключи ILER» в меню «Пуск».
ru.KeyAnthropic=Ключ Anthropic (Claude):
ru.KeyOpenAI=Ключ OpenAI:
ru.KeyGemini=Ключ Google Gemini:
ru.KeyCoins=Месячный лимит коинов (0 — без лимита):
ru.KeyBadChars=Ключ содержит недопустимые символы (пробел или кавычки?). Проверьте, что он вставлен целиком.
ru.KeyBadCoins=Лимит коинов должен быть целым числом.
ru.KeysIntroRe=ILER Bot уже установлен на этом компьютере. Заполните только то, что нужно сменить: пустое поле — оставить как есть (текущие ключи и лимит коинов сохраняются).
ru.KeyNone=Не задан ни один ключ: бот не сможет отвечать клиентам, пока техник ILER не добавит ключ. Продолжить?
ru.BadDir=Выберите папку с латинскими буквами, без кириллицы и специальных символов, например D:\ILER.
ru.NoStart=install.ps1 не запустился (политика выполнения или антивирус?). Журнал: %1
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
en.St_keys=Applying ILER keys...
en.ChangeKeys=Change ILER keys (technician)
en.KeysCaption=ILER keys
en.KeysDesc=Filled in by the ILER technician
en.KeysIntro=ILER's access keys to the language models and the client's monthly coin limit. They stay on this computer and are never shown in the admin UI. Leave a field empty to skip that provider.
en.KeyAnthropic=Anthropic (Claude) key:
en.KeyOpenAI=OpenAI key:
en.KeyGemini=Google Gemini key:
en.KeyCoins=Monthly coin limit (0 = no limit):
en.KeyBadChars=The key contains invalid characters (a space or quotes?). Make sure it was pasted completely.
en.KeyBadCoins=The coin limit must be a whole number.
en.KeysIntroRe=ILER Bot is already installed on this computer. Fill in only what should change: an empty field keeps the current value (the current keys and coin limit stay).
en.KeyNone=No key is set: the bot cannot answer clients until an ILER technician adds one. Continue?
en.BadDir=Choose a folder with Latin letters only, without Cyrillic or special characters, e.g. D:\ILER.
en.NoStart=install.ps1 did not start (execution policy or antivirus?). Log: %1
en.Starting=Starting installation...
en.DoneHeading=ILER Bot is installed
en.DoneText=The bot is running. Open the admin UI to create the owner and connect WhatsApp:%n%n%1%n%nThe desktop shortcut opens this link.
en.FailHeading=Installation did not finish
en.FailText=Error: %1%n%nFull log: %2%nSend it to support or run the installer again.
en.RebootText=Windows must restart to enable WSL. Setup continues automatically after you sign in.

[Icons]
; for the ILER technician: change the LLM keys / monthly coin limit later (runs deploy/set-llm-keys.sh)
Name: "{autoprograms}\ILER Bot\{cm:ChangeKeys}"; Filename: "{sys}\wsl.exe"; \
  Parameters: "-d Ubuntu-24.04 -u root -- bash /opt/iler/deploy/set-llm-keys.sh"; Comment: "{cm:ChangeKeys}"

[UninstallRun]
Filename: "{sys}\wsl.exe"; Parameters: "-d Ubuntu-24.04 -u root -- bash /opt/iler/deploy/install.sh --uninstall --yes"; \
  Flags: runhidden waituntilterminated; RunOnceId: "IlerUninstallStack"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ILER Bot - start WSL"" /F"; Flags: runhidden; RunOnceId: "IlerDelTaskStart"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ILER Bot - refresh portproxy"" /F"; Flags: runhidden; RunOnceId: "IlerDelTaskProxy"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""ILER Bot admin UI"""; Flags: runhidden; RunOnceId: "IlerDelFirewall"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ILER Bot - resume setup"" /F"; Flags: runhidden; RunOnceId: "IlerDelTaskResume"
; Windows 10 portproxy fallback (the GUI installer always uses the default port 8080)
Filename: "{sys}\netsh.exe"; Parameters: "interface portproxy delete v4tov4 listenport=8080 listenaddress=0.0.0.0"; Flags: runhidden; RunOnceId: "IlerDelPortProxy"
; Hyper-V firewall rule of the WSL mirrored mode
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-NetFirewallHyperVRule -Name ILER-Admin -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "IlerDelHyperVRule"
; the Ubuntu-24.04 distro (client database) is intentionally kept

[UninstallDelete]
Type: files; Name: "{commondesktop}\ILER Bot - *.url"
; install log, pending keys, portproxy refresh script
Type: filesandordirs; Name: "{commonappdata}\ILER"

[Code]
var
  KeysPage: TInputQueryWizardPage;
  ResultUrl, ErrorText: String;
  NeedReboot, InstallFailed, InstallerRan: Boolean;
  UrlEdit: TNewEdit;

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
  else if Stage = 'keys' then Result := CustomMessage('St_keys')
  else Result := '';
end;

// A previous installation exists: empty key fields then mean "keep the current keys".
function IsReinstall: Boolean;
begin
  Result := RegKeyExists(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F1C3E2A-9B7D-4C1E-A5F2-1D0B7C9E4A11}_is1');
  if (not Result) and (WizardForm <> nil) and (Trim(WizardForm.DirEdit.Text) <> '') then
    Result := FileExists(AddBackslash(Trim(WizardForm.DirEdit.Text)) + 'compose.yml');
end;

procedure InitializeWizard;
var
  Intro: String;
begin
  if IsReinstall then Intro := CustomMessage('KeysIntroRe') else Intro := CustomMessage('KeysIntro');
  KeysPage := CreateInputQueryPage(wpWelcome, CustomMessage('KeysCaption'), CustomMessage('KeysDesc'),
    Intro);
  KeysPage.Add(CustomMessage('KeyAnthropic'), True);
  KeysPage.Add(CustomMessage('KeyOpenAI'), True);
  KeysPage.Add(CustomMessage('KeyGemini'), True);
  KeysPage.Add(CustomMessage('KeyCoins'), False);
  // unattended installs: ILER-Setup.exe /SILENT /ANTHROPICKEY=... /COINS=30000
  KeysPage.Values[0] := ExpandConstant('{param:ANTHROPICKEY|}');
  KeysPage.Values[1] := ExpandConstant('{param:OPENAIKEY|}');
  KeysPage.Values[2] := ExpandConstant('{param:GEMINIKEY|}');
  KeysPage.Values[3] := ExpandConstant('{param:COINS|}');
end;

function ValidKey(const Value: String): Boolean;
var
  I: Integer;
  C: Char;
begin
  Result := True;
  for I := 1 to Length(Value) do
  begin
    C := Value[I];
    if not (((C >= 'A') and (C <= 'Z')) or ((C >= 'a') and (C <= 'z')) or ((C >= '0') and (C <= '9'))
            or (C = '-') or (C = '_') or (C = '.')) then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

function ValidCoins(const Value: String): Boolean;
var
  I: Integer;
begin
  Result := Length(Value) <= 9;
  for I := 1 to Length(Value) do
    if (Value[I] < '0') or (Value[I] > '9') then Result := False;
end;

// install.ps1, wslpath and the WSL disk location break on Cyrillic and special characters.
function ValidDir(const Value: String): Boolean;
var
  I: Integer;
  C: Char;
begin
  Result := Value <> '';
  for I := 1 to Length(Value) do
  begin
    C := Value[I];
    if not (((C >= 'A') and (C <= 'Z')) or ((C >= 'a') and (C <= 'z')) or ((C >= '0') and (C <= '9'))
            or (C = ':') or (C = '\') or (C = '.') or (C = '_') or (C = '-') or (C = ' ')) then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

// Trims the key fields and returns an error text ('' = valid). Used by the wizard and by silent installs.
function KeysError: String;
var
  I: Integer;
begin
  Result := '';
  for I := 0 to 3 do
    KeysPage.Values[I] := Trim(KeysPage.Values[I]);
  for I := 0 to 2 do
    if not ValidKey(KeysPage.Values[I]) then
    begin
      Result := CustomMessage('KeyBadChars');
      Exit;
    end;
  if not ValidCoins(KeysPage.Values[3]) then
    Result := CustomMessage('KeyBadCoins');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Err: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    if not ValidDir(WizardDirValue) then
    begin
      MsgBox(CustomMessage('BadDir'), mbError, MB_OK);
      Result := False;
    end;
    Exit;
  end;
  if CurPageID <> KeysPage.ID then Exit;
  Err := KeysError;
  if Err <> '' then
  begin
    MsgBox(Err, mbError, MB_OK);
    Result := False;
    Exit;
  end;
  // re-install: empty fields keep the current keys, no warning
  if (KeysPage.Values[0] = '') and (KeysPage.Values[1] = '') and (KeysPage.Values[2] = '') and (not IsReinstall) then
    Result := MsgBox(CustomMessage('KeyNone'), mbConfirmation, MB_YESNO) = IDYES;
end;

// Silent installs skip NextButtonClick: validate the /ANTHROPICKEY.. /COINS parameters and the folder here.
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := KeysError;
  if (Result = '') and (not ValidDir(WizardDirValue)) then Result := CustomMessage('BadDir');
end;

// KEY=VALUE file for install.ps1 (-KeysFile); it moves it to an admin-only place and deletes it once applied.
function WriteKeysFile(const FileName: String): Boolean;
var
  Lines: String;
begin
  Lines := '';
  if KeysPage.Values[0] <> '' then Lines := Lines + 'ILER_ANTHROPIC_KEY=' + KeysPage.Values[0] + #13#10;
  if KeysPage.Values[1] <> '' then Lines := Lines + 'ILER_OPENAI_KEY=' + KeysPage.Values[1] + #13#10;
  if KeysPage.Values[2] <> '' then Lines := Lines + 'ILER_GEMINI_KEY=' + KeysPage.Values[2] + #13#10;
  if KeysPage.Values[3] <> '' then Lines := Lines + 'ILER_MONTHLY_COINS=' + KeysPage.Values[3] + #13#10;
  Result := (Lines <> '') and SaveStringToFile(FileName, Lines, False);
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
  ProgressFile, KeysFile, Params, Line, Kind, Rest, Stage, Text: String;
  Finished, GotLine: Boolean;
begin
  InstallerRan := True;
  ProgressFile := ExpandConstant('{tmp}\progress.txt');
  DeleteFile(ProgressFile);
  Params := '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    ExpandConstant('{app}\deploy\windows\install.ps1') + '" -BundlePath "' + ExpandConstant('{app}') +
    '" -ProgressFile "' + ProgressFile + '" -DistroDir "' + ExpandConstant('{app}\wsl') + '"';
  KeysFile := ExpandConstant('{tmp}\iler-keys.env');
  if WriteKeysFile(KeysFile) then Params := Params + ' -KeysFile "' + KeysFile + '"';
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
    Pct := 0; Shown := 0; Ticks := 0; Finished := False; GotLine := False;
    while not Finished do
    begin
      Sleep(500);
      Ticks := Ticks + 1;
      Line := LastLine(ProgressFile);
      // install.ps1 writes "3|check|" right after it starts; nothing after 120 s: it never ran
      if (Line = '') and (not GotLine) and (Ticks >= 240) then
      begin
        InstallFailed := True;
        ErrorText := FmtMessage(CustomMessage('NoStart'), [ExpandConstant('{commonappdata}\ILER\install.log')]);
        Finished := True;
      end;
      if Line <> '' then
      begin
        GotLine := True;
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
    // a label cannot be selected: show the link in a read-only field so it can be copied (Ctrl+C)
    if UrlEdit = nil then
    begin
      UrlEdit := TNewEdit.Create(WizardForm);
      UrlEdit.Parent := WizardForm.FinishedPage;
      UrlEdit.ReadOnly := True;
    end;
    UrlEdit.Left := WizardForm.FinishedLabel.Left;
    UrlEdit.Width := WizardForm.FinishedLabel.Width;
    UrlEdit.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(8);
    UrlEdit.Text := ResultUrl;
    WizardForm.RunList.Top := UrlEdit.Top + UrlEdit.Height + ScaleY(12);
  end;
end;
