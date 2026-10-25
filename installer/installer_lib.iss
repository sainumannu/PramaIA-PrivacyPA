// ============================================================================
// PramaIA - funzioni Pascal riusabili negli installer Inno Setup
// ============================================================================
// Si include nella sezione [Code]:   #include SourcePath + "installer_lib.iss"
// Testate da tests\test_installer_lib.py (tests\installer_lib_harness.iss) in
// C:\PramaIA-Oracle, da cui questo file e' copiato cosi' com'e' (nessun
// riferimento specifico a una singola app).
//
// FetchPortalJwtSecret ottiene il segreto JWT condiviso dal Portal durante l'installazione,
// quando manca la variabile di sistema PRAMAIA_JWT_SECRET (Portal su un altro computer):
// lo chiede chi installa, da amministratore sul server, con le credenziali di servizio
// dell'app generate dall'admin del Portal. L'app non espone mai endpoint di configurazione.
//
// SecureAppDir/SecureSecretFile/SecureEnvFile blindano con icacls la cartella di
// installazione e le copie del file .env (OP02/OP03 di RICHIESTE_PLATFORM.md).
// ============================================================================

// Valore di KeyName in un file .env ('' se il file o la chiave mancano).
function GetEnvValue(FilePath, KeyName: String): String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line, Prefix: String;
begin
  Result := '';
  if not FileExists(FilePath) then
    Exit;
  if not LoadStringsFromFile(FilePath, Lines) then
    Exit;
  Prefix := KeyName + '=';
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Lines[I];
    if Copy(Line, 1, Length(Prefix)) = Prefix then
    begin
      Result := Trim(Copy(Line, Length(Prefix) + 1, Length(Line) - Length(Prefix)));
      Exit;
    end;
  end;
end;

// Imposta KeyName=Value in un file .env esistente: sostituisce la riga della chiave o la
// aggiunge in fondo, lasciando invariate le altre.
function SetEnvValue(FilePath, KeyName, Value: String): Boolean;
var
  Lines: TArrayOfString;
  I, Count: Integer;
  Prefix: String;
  Found: Boolean;
begin
  Result := False;
  if not LoadStringsFromFile(FilePath, Lines) then
    Exit;
  Prefix := KeyName + '=';
  Found := False;
  Count := GetArrayLength(Lines);
  for I := 0 to Count - 1 do
  begin
    if Copy(Lines[I], 1, Length(Prefix)) = Prefix then
    begin
      Lines[I] := Prefix + Value;
      Found := True;
    end;
  end;
  if not Found then
  begin
    SetArrayLength(Lines, Count + 1);
    Lines[Count] := Prefix + Value;
  end;
  Result := SaveStringsToFile(FilePath, Lines, False);
end;

// Valore stringa di Key in un JSON piatto ('' se manca o non e' una stringa). Le sequenze di
// escape non vengono decodificate: e' chi chiama a decidere se il valore e' accettabile.
function ExtractJsonString(Json, Key: String): String;
var
  P: Integer;
  Rest: String;
begin
  Result := '';
  P := Pos('"' + Key + '"', Json);
  if P = 0 then
    Exit;
  Rest := TrimLeft(Copy(Json, P + Length(Key) + 2, Length(Json)));
  if Copy(Rest, 1, 1) <> ':' then
    Exit;
  Rest := TrimLeft(Copy(Rest, 2, Length(Rest)));
  if Copy(Rest, 1, 1) <> '"' then
    Exit;
  Rest := Copy(Rest, 2, Length(Rest));
  P := Pos('"', Rest);
  if P = 0 then
    Exit;
  Result := Copy(Rest, 1, P - 1);
end;

// Chiede al Portal il segreto JWT condiviso: GET {PortalUrl}/api/platform/jwt-secret con le
// credenziali di servizio dell'app (X-App-ID, X-App-Secret). Se fallisce, ErrorMessage spiega
// a chi installa cosa fare.
function FetchPortalJwtSecret(PortalUrl, AppId, AppSecret: String; var JwtSecret, ErrorMessage: String): Boolean;
var
  Http: Variant;
  Url, Body, Detail: String;
  Status: Integer;
begin
  Result := False;
  JwtSecret := '';
  ErrorMessage := '';
  Url := Trim(PortalUrl);
  while (Length(Url) > 0) and (Copy(Url, Length(Url), 1) = '/') do
    Url := Copy(Url, 1, Length(Url) - 1);
  Url := Url + '/api/platform/jwt-secret';

  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    // risoluzione, connessione, invio, ricezione (millisecondi)
    Http.SetTimeouts(5000, 5000, 15000, 15000);
    Http.Open('GET', Url, False);
    Http.SetRequestHeader('X-App-ID', AppId);
    Http.SetRequestHeader('X-App-Secret', AppSecret);
    Http.Send('');
    Status := Http.Status;
    Body := Http.ResponseText;
  except
    ErrorMessage := 'Impossibile contattare il Portal (' + Url + '):' + #13#10 + GetExceptionMessage + #13#10 + #13#10 +
      'Verifica l''URL del Portal e che sia raggiungibile da questo computer.';
    Exit;
  end;

  Detail := ExtractJsonString(Body, 'detail');
  if Status = 200 then
  begin
    JwtSecret := ExtractJsonString(Body, 'jwt_secret');
    if JwtSecret = '' then
    begin
      // Un Portal senza questo endpoint risponde con la pagina dell'interfaccia (200, HTML)
      ErrorMessage := 'Il Portal non ha restituito il segreto JWT (' + Url + ').' + #13#10 + #13#10 +
        'Verifica che l''URL sia quello del Portal e che il Portal sia aggiornato: le versioni ' +
        'precedenti non consegnano il segreto alle app.';
      Exit;
    end;
    if Pos('\', JwtSecret) > 0 then
    begin
      JwtSecret := '';
      ErrorMessage := 'Il segreto JWT restituito dal Portal contiene caratteri non gestiti dall''installer.';
      Exit;
    end;
    Result := True;
  end
  else if Status = 401 then
    ErrorMessage := 'Il Portal ha rifiutato le credenziali dell''app "' + AppId + '": ' + Detail + #13#10 + #13#10 +
      'Verifica l''App Secret, oppure chiedi all''admin del Portal di generarne uno per questa app.'
  else if (Status = 404) and (Detail = 'Not Found') then
    ErrorMessage := 'Il Portal non consegna il segreto alle app (' + Url + ' non esiste).' + #13#10 + #13#10 +
      'Verifica l''URL del Portal o aggiornalo.'
  else if Status = 404 then
    ErrorMessage := 'L''app "' + AppId + '" non e'' registrata nel Portal o non e'' attiva.' + #13#10 + #13#10 +
      'L''admin del Portal deve registrarla con questo ID e generarne l''App Secret.'
  else
  begin
    ErrorMessage := 'Il Portal ha risposto con errore ' + IntToStr(Status);
    if Detail <> '' then
      ErrorMessage := ErrorMessage + ': ' + Detail
    else
      ErrorMessage := ErrorMessage + '.';
  end;
end;

// La cartella contiene l'eseguibile del servizio, che gira come SYSTEM: solo SYSTEM e Administrators
// possono modificarla, gli utenti solo leggerla. Ordine dei comandi CRITICO: prima i permessi
// espliciti, poi via quelli ereditati (altrimenti nessuno potrebbe piu' attraversare le
// sottocartelle), infine via eventuali permessi espliciti di Authenticated Users ed Everyone in
// tutto l'albero. Non invertire l'ordine: bloccherebbe l'installer stesso fuori dalla cartella.
procedure SecureAppDir();
var
  ResultCode: Integer;
  AppDir, IcaclsPath: String;
begin
  AppDir := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  if Length(AppDir) <= 3 then
  begin
    Log('[WARNING] Cartella di installazione alla radice di un disco: permessi non modificati');
    Exit;
  end;
  IcaclsPath := ExpandConstant('{sys}\icacls.exe');
  Exec(IcaclsPath, '"' + AppDir + '" /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(IcaclsPath, '"' + AppDir + '" /inheritance:r', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(IcaclsPath, '"' + AppDir + '" /remove:g *S-1-5-11 *S-1-1-0 /T /C /Q', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Solo SYSTEM (il servizio) e Administrators possono leggere i file che contengono il segreto.
// SID al posto dei nomi dei gruppi, che cambiano con la lingua di Windows.
procedure SecureSecretFile(FilePath: String);
var
  ResultCode: Integer;
begin
  if FileExists(FilePath) then
  begin
    Exec(ExpandConstant('{sys}\icacls.exe'), '"' + FilePath + '" /inheritance:r', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\icacls.exe'), '"' + FilePath + '" /grant *S-1-5-18:(F)', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\icacls.exe'), '"' + FilePath + '" /grant *S-1-5-32-544:(F)', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

// Anche .env.backup e .env.new (quando esistono) contengono il segreto: CopyFile/
// SaveStringToFile non copiano i permessi del .env, quindi erediterebbero quelli della
// cartella (leggibili dagli utenti). SecureSecretFile non fa nulla sui file assenti.
procedure SecureEnvFile();
begin
  SecureSecretFile(ExpandConstant('{app}\.env'));
  SecureSecretFile(ExpandConstant('{app}\.env.backup'));
  SecureSecretFile(ExpandConstant('{app}\.env.new'));
end;
