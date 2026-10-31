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
#define MyAppVersion "1.0.9"
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
  DataPage: TInputOptionWizardPage;
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

// Percorso del database SQLite di un'installazione esistente, da DATABASE_URL del suo .env ('' se non e' SQLite).
// Il servizio gira con la cartella di installazione come directory di lavoro: i percorsi relativi partono da li'.
function DatabasePath(AppDir: String): String;
var
  Url: String;
  P: Integer;
begin
  Result := '';
  Url := GetEnvValue(AppDir + '.env', 'DATABASE_URL');
  if Url = '' then
    Url := 'sqlite:///./{#MyAppName}.db';
  P := Pos(':///', Url);
  if (Pos('sqlite', LowerCase(Url)) <> 1) or (P = 0) then
    Exit;
  Url := Copy(Url, P + 4, MaxInt);
  StringChangeEx(Url, '/', '\', True);
  if Copy(Url, 1, 2) = '.\' then
    Result := AppDir + Copy(Url, 3, MaxInt)
  else if Pos(':', Url) > 0 then
    Result := Url
  else
    Result := AppDir + Url;
end;

// True se nella cartella scelta c'e' gia' un'installazione con dati (database o documenti caricati).
function HasExistingData(): Boolean;
var
  AppDir, Db: String;
begin
  AppDir := AddBackslash(WizardDirValue());
  Db := DatabasePath(AppDir);
  Result := ((Db <> '') and FileExists(Db)) or DirExists(AppDir + 'data\documents');
end;

// True se l'utente ha scelto di ripartire da un database vuoto.
function WantsFreshData(): Boolean;
begin
  Result := (DataPage <> nil) and HasExistingData() and (DataPage.SelectedValueIndex = 1);
end;

function MoveAside(Source, Dest: String): Boolean;
begin
  Result := True;
  if FileExists(Source) or DirExists(Source) then
    Result := RenameFile(Source, Dest);
end;

// Nuova installazione: database e documenti non si cancellano, si spostano in backups\ (recuperabili).
// Restano .env (collegamento al Portal), i backup gia' fatti e i log. Restituisce un messaggio d'errore o ''.
function MoveExistingData(): String;
var
  AppDir, Db, Target, Name: String;
begin
  Result := '';
  AppDir := AddBackslash(WizardDirValue());
  Target := AppDir + 'backups\prima-della-nuova-installazione-' + GetDateTimeString('yyyymmdd-hhnnss', #0, #0) + '\';
  if not ForceDirectories(Target) then
  begin
    Result := 'Impossibile creare la cartella ' + Target;
    Exit;
  end;
  Db := DatabasePath(AppDir);
  if Db <> '' then
  begin
    Name := ExtractFileName(Db);
    if not (MoveAside(Db, Target + Name) and MoveAside(Db + '-wal', Target + Name + '-wal') and
            MoveAside(Db + '-shm', Target + Name + '-shm') and MoveAside(Db + '-journal', Target + Name + '-journal')) then
    begin
      Result := 'Impossibile spostare il database ' + Db + ' in ' + Target +
                '. Nessun file e'' stato installato: chiudere i programmi che lo usano e riprovare.';
      Exit;
    end;
  end;
  if not MoveAside(AppDir + 'data\documents', Target + 'documents') then
    Result := 'Impossibile spostare i documenti (' + AppDir + 'data\documents) in ' + Target + '.';
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

  // Mostrata solo se nella cartella c'e' gia' un'installazione con dati (vedi ShouldSkipPage)
  DataPage := CreateInputOptionPage(
    wpSelectDir,
    'Installazione esistente',
    'In questa cartella {#MyAppName} e'' gia'' installato e ha dei dati.',
    'Scegli cosa fare dei dati esistenti (registro dei trattamenti, documenti, ruoli, autorizzazioni).' + #13#10 + #13#10 +
    'Con una nuova installazione il database e i documenti attuali non vengono cancellati: vengono spostati ' +
    'nella cartella backups\prima-della-nuova-installazione-<data> e si possono recuperare. ' +
    'La configurazione (collegamento al Portal) e i backup gia'' fatti restano.',
    True, False);
  DataPage.Add('Aggiorna e conserva i dati (consigliato)');
  DataPage.Add('Nuova installazione: riparti da un database vuoto');
  DataPage.SelectedValueIndex := 0;

  PortalUrlPage := CreateInputQueryPage(
    DataPage.ID,
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
  if PageID = DataPage.ID then
    Result := not HasExistingData();
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

  if (CurPageID = DataPage.ID) and (DataPage.SelectedValueIndex = 1) then
  begin
    Result := MsgBox('Ripartire da un database vuoto?' + #13#10 + #13#10 +
                     'Il registro, i documenti e tutti gli altri dati attuali spariranno da {#MyAppName}. ' +
                     'Restano recuperabili nella cartella backups\prima-della-nuova-installazione-<data>.',
                     mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    Exit;
  end;

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

// Esegue un comando con cmd.exe e restituisce il suo codice di uscita (-1 se non parte).
function RunHidden(Command: String): Integer;
begin
  if not Exec(ExpandConstant('{cmd}'), '/C ' + Command, '', SW_HIDE, ewWaitUntilTerminated, Result) then
    Result := -1;
end;

// find.exe di Windows per percorso completo: nel PATH potrebbe esserci un altro find (Git, Cygwin)
function FindExe(): String;
begin
  Result := '"' + ExpandConstant('{sys}ind.exe') + '"';
end;

function ServiceExists(): Boolean;
begin
  Result := RunHidden('sc.exe query {#MyServiceName}') = 0;
end;

function ServiceStopped(): Boolean;
begin
  Result := RunHidden('sc.exe query {#MyServiceName} | ' + FindExe() + ' "STOPPED"') = 0;
end;

function AppProcessRunning(): Boolean;
begin
  // In formato CSV il nome non e' troncato a 25 caratteri come nella tabella
  Result := RunHidden('tasklist /FI "IMAGENAME eq {#MyAppExeName}" /FO CSV /NH | ' + FindExe() + ' /I "{#MyAppExeName}"') = 0;
end;

// Un file e' libero se si apre in scrittura esclusiva (un eseguibile in uso si puo' rinominare, ma non aprire cosi'); assente = libero.
function FileIsFree(Path: String): Boolean;
var
  Stream: TFileStream;
begin
  Result := True;
  if not FileExists(Path) then
    Exit;
  try
    Stream := TFileStream.Create(Path, fmOpenReadWrite or fmShareExclusive);
    Stream.Free;
  except
    Result := False;
  end;
end;

function AppFilesFree(): Boolean;
var
  AppDir: String;
begin
  AppDir := ExpandConstant('{app}\');
  Result := FileIsFree(AppDir + '{#MyAppExeName}') and FileIsFree(AppDir + 'nssm.exe');
end;

// Ferma servizio e processi PRIMA della copia dei file, e aspetta che lo siano davvero:
// sc stop non attende, e se si uccide il processo con il servizio ancora attivo NSSM lo fa ripartire.
function StopAppBeforeCopy(): String;
var
  I: Integer;
begin
  Result := '';
  if ServiceExists() then
  begin
    RunHidden('sc.exe stop {#MyServiceName}');
    for I := 1 to 120 do          // fino a 60 secondi
    begin
      if ServiceStopped() then
        Break;
      Sleep(500);
    end;
    if not ServiceStopped() then
    begin
      // Servizio bloccato in arresto: si chiude il processo di NSSM di questo servizio
      RunHidden('taskkill /F /FI "SERVICES eq {#MyServiceName}"');
      Sleep(2000);
    end;
  end;

  // Processi dell'app avviati a mano (start-service.cmd) o rimasti appesi
  for I := 1 to 30 do             // fino a 15 secondi
  begin
    if not AppProcessRunning() then
      Break;
    RunHidden('taskkill /F /T /IM {#MyAppExeName}');
    Sleep(500);
  end;

  // Windows rilascia i file con qualche istante di ritardo
  for I := 1 to 40 do             // fino a 20 secondi
  begin
    if AppFilesFree() then
      Exit;
    Sleep(500);
  end;
  Result := 'Non riesco a fermare {#MyAppName}: i suoi file sono ancora in uso.' + #13#10 + #13#10 +
            'Ferma il servizio "{#MyServiceName}" (Servizi di Windows, oppure "sc stop {#MyServiceName}" da amministratore), ' +
            'chiudi eventuali finestre del programma e riavvia l''installazione.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;

  { 1) Servizio e processi fermi e file liberi, altrimenti ci si ferma qui con un messaggio chiaro }
  Result := StopAppBeforeCopy();
  if Result <> '' then
    Exit;

  { 2) Nuova installazione: dati esistenti spostati in backups\ (a servizio fermo, prima della copia) }
  if WantsFreshData() then
    Result := MoveExistingData();
end;

// Riepilogo prima dell'installazione: cosa succede ai dati.
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine;
  if HasExistingData() then
  begin
    if WantsFreshData() then
      Result := Result + 'Dati esistenti:' + NewLine + Space +
                'spostati in backups\prima-della-nuova-installazione-<data>; si riparte da un database vuoto' + NewLine + NewLine
    else
      Result := Result + 'Dati esistenti:' + NewLine + Space + 'conservati (aggiornamento)' + NewLine + NewLine;
  end;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine;
end;











