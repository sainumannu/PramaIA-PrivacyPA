; ============================================================================
; PramaIA-PrivacyManager - Inno Setup Installer Script
; ============================================================================
; Stesso schema di C:\PramaIA-SysAdmin2\installer\installer.iss (PyInstaller +
; NSSM + Inno Setup). Richiede Inno Setup 6: https://jrsoftware.org/isdl.php
;
; Utilizzo manuale:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; La versione viene sovrascritta automaticamente da build-release.ps1
; ============================================================================

#define MyAppName      "PramaIA-PrivacyManager"
#define MyAppVersion "1.0.4"
#define MyAppPublisher "PramaIA Platform"
#define MyAppURL       "https://pramaia.ai"
#define MyAppExeName   "PramaIA-PrivacyManager.exe"
#define MyServiceName  "PramaIA-PrivacyManager"
#define ReleaseDir     "..\release"
#define AppUrl         "http://127.0.0.1:8003/"

[Setup]
AppId={{2A3A0010-030D-4FCF-B8D9-DEEECE1B47BC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\PramaIA\PrivacyManager
DefaultGroupName=PramaIA
DisableDirPage=no
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=PramaIA-PrivacyManager-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Types]
Name: "full";     Description: "Installazione completa"
Name: "compact";  Description: "Installazione minima"

[Components]
Name: "service";  Description: "PramaIA-PrivacyManager (Backend + interfaccia web)";  Types: full compact; Flags: fixed

[Tasks]
Name: "installservice";   Description: "Installa come servizio Windows";  GroupDescription: "Servizio Windows:"
Name: "desktopicon";      Description: "Crea icona sul Desktop";         GroupDescription: "Icone aggiuntive:"

[Files]
; --- nssm.exe (Windows Service wrapper) ---
Source: "nssm.exe";                              DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service

; --- Backend compilato (PyInstaller --onedir: exe + _internal, incluso il build del frontend) ---
Source: "{#ReleaseDir}\{#MyAppExeName}";        DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "{#ReleaseDir}\_internal\*";            DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Components: service

; --- Configurazione e documentazione ---
Source: "{#ReleaseDir}\.env.example";           DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "{#ReleaseDir}\version.txt";            DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "{#ReleaseDir}\INSTALLER_README.md";    DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "{#ReleaseDir}\README.md";              DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "{#ReleaseDir}\RUNBOOK_RIPRISTINO.md";  DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service

; --- Script di avvio e gestione servizio ---
Source: "start-service.ps1";      DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "start-service.cmd";      DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "service.ps1";            DestDir: "{app}";           Flags: ignoreversion skipifsourcedoesntexist; Components: service

; --- Cartelle persistenti ---
Source: "{#ReleaseDir}\logs\.gitkeep";          DestDir: "{app}\logs";      Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "{#ReleaseDir}\data\.gitkeep";          DestDir: "{app}\data";      Flags: ignoreversion skipifsourcedoesntexist; Components: service
Source: "{#ReleaseDir}\backups\.gitkeep";       DestDir: "{app}\backups";   Flags: ignoreversion skipifsourcedoesntexist; Components: service

[Dirs]
Name: "{app}\logs";        Flags: uninsneveruninstall
Name: "{app}\data";        Flags: uninsneveruninstall
Name: "{app}\backups";     Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#MyAppName}";         Filename: "{#AppUrl}";                Tasks: installservice
Name: "{group}\{#MyAppName}";         Filename: "{app}\start-service.cmd";  Tasks: not installservice
Name: "{group}\Apri interfaccia web"; Filename: "{#AppUrl}";                Tasks: not installservice
Name: "{group}\Disinstalla";          Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{#AppUrl}";                Tasks: desktopicon and installservice
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\start-service.cmd";  Tasks: desktopicon and not installservice

[Run]
; Scrive .env (segreto JWT + URL del Portal) e blinda i permessi di cartella/.env
; PRIMA che nssm installi/avvii il servizio sotto (il servizio deve trovare la
; configurazione gia' presente). Check restituisce sempre False: l'eseguibile di
; questa riga non viene mai lanciato, serve solo a eseguire codice nel punto giusto.
Filename: "{sys}\cmd.exe"; Parameters: "/c exit /b 0"; Flags: runhidden; Check: PrepareEnvBeforeService
; Installa come servizio Windows se richiesto
Filename: "{app}\nssm.exe"; Parameters: "install {#MyServiceName} ""{app}\{#MyAppExeName}"""; Tasks: installservice; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppDirectory ""{app}"""; Tasks: installservice; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStdout ""{app}\logs\service.log"""; Tasks: installservice; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStderr ""{app}\logs\service-error.log"""; Tasks: installservice; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "start {#MyServiceName}"; Tasks: installservice; Flags: runhidden
; Regola del firewall (§ accesso da altre macchine): un servizio Windows non interattivo (via NSSM)
; non fa mai comparire il prompt "Consenti l'accesso" che vedrebbe un'app grafica: senza questa regola
; esplicita, Windows Firewall blocca in silenzio le connessioni in ingresso da altre macchine sulla rete.
Filename: "netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#MyAppName}"" dir=in action=allow program=""{app}\{#MyAppExeName}"" enable=yes profile=any"; Flags: runhidden
; NOTA: NON avviare start-service.cmd se e' gia' installato come servizio
; (doppio processo sulla stessa porta -> WinError 10048). Solo se NON si e' scelto il servizio.
Filename: "cmd.exe"; Parameters: "/c ""{app}\start-service.cmd"""; Description: "Avvia {#MyAppName}"; Tasks: not installservice; Flags: nowait postinstall skipifsilent
; Apertura del browser a fine installazione: al primo avvio l'interfaccia mostra
; il wizard di configurazione. BeforeInstall attende che il backend risponda.
Filename: "{#AppUrl}"; Description: "Avvia la configurazione guidata"; Check: NeedsSetup; BeforeInstall: WaitBeforeOpen; Flags: postinstall shellexec nowait skipifsilent
Filename: "{#AppUrl}"; Description: "Apri {#MyAppName}"; Check: not NeedsSetup; BeforeInstall: WaitBeforeOpen; Flags: postinstall shellexec nowait skipifsilent

[UninstallRun]
; Disinstalla il servizio Windows se installato
Filename: "{app}\nssm.exe"; Parameters: "stop {#MyServiceName}"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove {#MyServiceName} confirm"; Flags: runhidden
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#MyAppName}"""; Flags: runhidden

; ============================================================================
; CRITICAL: stop service + kill processes BEFORE [Files] copy.
; [Run] is too late — nssm.exe / PramaIA-PrivacyManager.exe stay locked.
; ============================================================================
[Code]
#include SourcePath + "installer_lib.iss"

const
  SetupCheckUrl = '{#AppUrl}api/setup/check';
  ServiceWaitSeconds = 90;

var
  PortalUrlPage: TInputQueryWizardPage;
  JwtPage: TInputQueryWizardPage;
  // Segreto ottenuto dal Portal nella pagina JwtPage (vuoto se la pagina e' stata saltata)
  FetchedJwtSecret: String;

function GetPortalUrl(): String;
begin
  if PortalUrlPage <> nil then
    Result := Trim(PortalUrlPage.Values[0])
  else
    Result := '';
end;

function IsHttpUrl(Value: String): Boolean;
begin
  Result := (Pos('http://', LowerCase(Value)) = 1) or (Pos('https://', LowerCase(Value)) = 1);
end;

// UI Portal :3080 -> API backend :8091 (porta reale di questa piattaforma), come
// derive_portal_api_url() in backend/routers/setup_router.py.
function DerivePortalApiUrl(PortalUrl: String): String;
var
  Url: String;
begin
  Url := PortalUrl;
  while (Length(Url) > 0) and (Copy(Url, Length(Url), 1) = '/') do
    Url := Copy(Url, 1, Length(Url) - 1);
  if Pos(':3080', Url) > 0 then
    StringChangeEx(Url, ':3080', ':8091', True);
  Result := Url;
end;

// Segreto JWT da scrivere nel .env. Non lo digita mai l'utente:
//   1. variabile di sistema PRAMAIA_JWT_SECRET, creata dall'installer del Portal sullo stesso computer;
//   2. altrimenti quello ottenuto dal Portal nella pagina JwtPage (Portal su un altro computer);
//   3. altrimenti quello gia' presente nel .env di un'installazione precedente.
function ResolveJwtSecret(ExistingEnvPath: String): String;
begin
  Result := GetEnv('PRAMAIA_JWT_SECRET');
  if Result = '' then
    Result := FetchedJwtSecret;
  if Result = '' then
    Result := GetEnvValue(ExistingEnvPath, 'PRAMAIA_JWT_SECRET');
end;

procedure InitializeWizard();
var
  PrevEnvPath, PrevPortalUrl: String;
  JwtDesc: String;
begin
  FetchedJwtSecret := '';
  // WizardDirValue() e' gia' disponibile qui (a differenza di {app}, non ancora
  // inizializzata): con UsePreviousAppDir contiene gia' la cartella precedente
  // se e' un aggiornamento.
  PrevEnvPath := AddBackslash(WizardDirValue()) + '.env';
  PrevPortalUrl := GetEnvValue(PrevEnvPath, 'PORTAL_URL');

  PortalUrlPage := CreateInputQueryPage(
    wpSelectDir,
    'Configurazione Portal',
    'Specifica l''URL completo dove il PramaIA Portal e'' raggiungibile.',
    '{#MyAppName} si autentica esclusivamente tramite il Portal (SSO).' + #13#10 + #13#10 +
    'Esempio: http://localhost:3080 (stesso computer) oppure http://192.168.1.100:3080 (in rete).'
  );
  PortalUrlPage.Add('Portal URL:', False);
  if PrevPortalUrl <> '' then
    PortalUrlPage.Values[0] := PrevPortalUrl
  else
    PortalUrlPage.Values[0] := 'http://localhost:3080';

  // Mostrata solo se il segreto non e' gia' disponibile (vedi ShouldSkipPage)
  JwtDesc := 'Su questo computer non c''e'' la variabile di sistema PRAMAIA_JWT_SECRET, che l''installer ' +
    'del Portal crea: il Portal e'' probabilmente su un altro computer.' + #13#10 + #13#10 +
    'L''installer puo'' chiedere il segreto al Portal indicato nella pagina precedente. Serve l''App Secret ' +
    'di {#MyServiceName}, che l''admin del Portal genera dopo aver registrato l''app con questo ID. ' +
    'L''App Secret serve solo ora e non viene salvato.' + #13#10 + #13#10 +
    'Lascia vuoto per proseguire senza: il servizio verra'' installato ma non potra'' autenticarsi finche'' ' +
    'il segreto non sara'' impostato (anche a mano nel .env, o rieseguendo questo installer).';

  JwtPage := CreateInputQueryPage(
    PortalUrlPage.ID,
    'Segreto JWT del Portal',
    '{#MyAppName} deve conoscere il segreto con cui il Portal firma gli accessi.',
    JwtDesc
  );
  JwtPage.Add('App Secret di {#MyServiceName} (fornito dall''admin del Portal):', True);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  // Qui la cartella scelta e' gia' nota: il segreto puo' venire dal .env di un'installazione precedente
  if PageID = JwtPage.ID then
    Result := (GetEnv('PRAMAIA_JWT_SECRET') <> '') or
              (GetEnvValue(AddBackslash(WizardDirValue()) + '.env', 'PRAMAIA_JWT_SECRET') <> '');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ErrorMessage: String;
begin
  Result := True;

  if CurPageID = PortalUrlPage.ID then
  begin
    if (Trim(PortalUrlPage.Values[0]) = '') or not IsHttpUrl(PortalUrlPage.Values[0]) then
    begin
      MsgBox('Specifica un URL del Portal valido (es. http://localhost:3080).', mbError, MB_OK);
      Result := False;
    end;
    Exit;
  end;

  if CurPageID = JwtPage.ID then
  begin
    FetchedJwtSecret := '';
    if Trim(JwtPage.Values[0]) = '' then
    begin
      Result := MsgBox('Proseguire senza il segreto JWT del Portal?' + #13#10 + #13#10 +
                       '{#MyAppName} verra'' installato ma non potra'' autenticarsi finche'' il segreto non ' +
                       'sara'' nel file .env: per ottenerlo basta rieseguire questo installer con l''App Secret.',
                       mbConfirmation, MB_YESNO) = IDYES;
      Exit;
    end;
    WizardForm.NextButton.Enabled := False;
    try
      Result := FetchPortalJwtSecret(GetPortalUrl(), '{#MyServiceName}', Trim(JwtPage.Values[0]),
                                     FetchedJwtSecret, ErrorMessage);
    finally
      WizardForm.NextButton.Enabled := True;
    end;
    if not Result then
      MsgBox(ErrorMessage, mbError, MB_OK)
    else
      MsgBox('Segreto JWT ottenuto dal Portal.', mbInformation, MB_OK);
  end;
end;

// Non si tocca mai un .env esistente (potrebbe essere un aggiornamento con
// configurazione gia' presente, coerente col resto di questo installer): se
// c'e' gia', al massimo si integra il segreto se mancava anche li'.
procedure CreateEnvFileIfMissing();
var
  EnvPath, JwtSecret, PortalUrl, EnvContent: String;
begin
  EnvPath := ExpandConstant('{app}\.env');
  if FileExists(EnvPath) then
  begin
    JwtSecret := ResolveJwtSecret(EnvPath);
    if (JwtSecret <> '') and (GetEnvValue(EnvPath, 'PRAMAIA_JWT_SECRET') = '') then
      SetEnvValue(EnvPath, 'PRAMAIA_JWT_SECRET', JwtSecret);
    Exit;
  end;

  PortalUrl := GetPortalUrl();
  JwtSecret := ResolveJwtSecret(EnvPath);

  EnvContent :=
    '# {#MyAppName} - Configurazione' + #13#10 +
    '# Generato automaticamente dall''installer' + #13#10 +
    '' + #13#10 +
    'PRAMAIA_JWT_SECRET=' + JwtSecret + #13#10 +
    'PORTAL_URL=' + PortalUrl + #13#10 +
    'PORTAL_API_URL=' + DerivePortalApiUrl(PortalUrl) + #13#10 +
    'DATABASE_URL=sqlite:///./PramaIA-PrivacyManager.db' + #13#10 +
    'APP_ID={#MyServiceName}' + #13#10;

  SaveStringToFile(EnvPath, EnvContent, False);
end;

function PrepareEnvBeforeService(): Boolean;
begin
  // Non eseguire mai cmd.exe: questa riga [Run] serve solo a lanciare questo
  // codice nel punto giusto della sequenza (dopo la copia file, prima di nssm).
  Result := False;
  CreateEnvFileIfMissing();
  SecureAppDir();
  SecureEnvFile();
end;

{ GET su SetupCheckUrl: restituisce lo status HTTP (0 se il backend non risponde). }
function GetSetupCheck(var Body: String): Integer;
var
  Req: Variant;
begin
  Result := 0;
  Body := '';
  try
    Req := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Req.SetProxy(1); { connessione diretta: localhost non passa dal proxy }
    Req.SetTimeouts(2000, 2000, 2000, 2000);
    Req.Open('GET', SetupCheckUrl, False);
    Req.Send('');
    Result := Req.Status;
    Body := Req.ResponseText;
  except
    Result := 0;
  end;
end;

function WaitForBackend(Seconds: Integer): Boolean;
var
  I: Integer;
  Body: String;
begin
  Result := False;
  for I := 1 to Seconds do
  begin
    if GetSetupCheck(Body) = 200 then
    begin
      Result := True;
      Exit;
    end;
    Sleep(1000);
  end;
end;

{ True se l'app non e' ancora configurata (primo avvio): chiede al backend e,
  se non risponde (avvio manuale non ancora eseguito), guarda se esiste il .env. }
function NeedsSetup: Boolean;
var
  Body: String;
begin
  if GetSetupCheck(Body) = 200 then
    Result := Pos('"is_configured":false', Body) > 0
  else
    Result := not FileExists(ExpandConstant('{app}\.env'));
end;

procedure WaitBeforeOpen;
begin
  WaitForBackend(ServiceWaitSeconds);
end;

{ Con il servizio installato la pagina finale compare solo quando il backend
  risponde: cosi' il browser si apre direttamente sul wizard. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('installservice') then
  begin
    WizardForm.StatusLabel.Caption := 'Avvio del servizio {#MyAppName} in corso...';
    WaitForBackend(ServiceWaitSeconds);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;

  { 1) Stop Windows service if present }
  Exec('sc.exe', 'stop {#MyServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);

  { 2) Kill residual app processes that hold file locks }
  Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);

  { 3) Brief wait for Windows to release handles }
  Sleep(1000);
end;






