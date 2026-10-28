# PramaIA-PrivacyManager — Guida Installer Windows

**Leggere prima di installare o aggiornare.**
File installer: `installer_output\PramaIA-PrivacyManager-Setup-<versione>.exe`
Directory default: `C:\Program Files\PramaIA\PrivacyManager`
Porta: **8003** (interfaccia web + API sullo stesso indirizzo) · Servizio: **PramaIA-PrivacyManager**

---

## 0. Prerequisiti (da avere pronto, non incluso nell'installer)

| Requisito | Note |
|-----------|------|
| **PramaIA-Portal** raggiungibile | Login e licenza passano dal Portal. Deve essere raggiungibile **dal server** (non `localhost` se il Portal è su un'altra macchina). Senza Portal raggiungibile il servizio parte comunque, ma il wizard non riesce a completare la configurazione automatica e il login non funziona. |

**Nessun database esterno da installare**: PrivacyManager usa **SQLite locale** (`data\PramaIA-PrivacyManager.db`), creato automaticamente al primo avvio. Non serve PostgreSQL: il sistema di backup integrato (§57, vedi `RUNBOOK_RIPRISTINO.md`) supporta solo SQLite e non funziona con un altro database.

---

## 1. Installazione / aggiornamento (ordine corretto)

| Passo | Cosa succede |
|-------|----------------|
| 1 | **Stop servizio + kill processi** (obbligatorio *prima* della copia file) |
| 2 | Copia `PramaIA-PrivacyManager.exe`, `_internal\`, `nssm.exe`, … |
| 3 | (Opzionale, selezionato di default) installazione/riavvio del servizio Windows via NSSM |
| 4 | Apertura del browser sull'app appena avviata |

Questo lo fa da solo la sezione **`PrepareToInstall`** di `installer.iss` (gira **prima** di copiare i file):
1. `sc stop PramaIA-PrivacyManager`
2. `taskkill /F /IM PramaIA-PrivacyManager.exe`
3. breve attesa per rilasciare i lock su disco

Se la copia file fallisce comunque, PowerShell **come amministratore**:

```powershell
Stop-Service PramaIA-PrivacyManager -Force -ErrorAction SilentlyContinue
sc.exe stop PramaIA-PrivacyManager
taskkill /F /IM PramaIA-PrivacyManager.exe 2>$null
# poi riesegui il Setup come admin
```

**Installazione esistente: conservare i dati o ripartire da zero.** Se nella cartella scelta c'è già un'installazione con dati (il database indicato da `DATABASE_URL` nel `.env`, oppure documenti in `data\documents`), il Setup mostra la pagina *Installazione esistente* con due scelte:

| Scelta | Cosa succede |
|--------|--------------|
| **Aggiorna e conserva i dati** (predefinita) | Si sostituiscono solo i programmi; database, documenti, `.env`, log e backup restano come sono. |
| **Nuova installazione** | Si riparte da un database vuoto. Database (con i file `-wal`/`-shm`) e documenti **non vengono cancellati**: a servizio fermo vengono spostati in `backups\prima-della-nuova-installazione-<aaaammgg-hhmmss>\`. Restano `.env` (collegamento al Portal), i backup già fatti e i log. |

Per recuperare i dati dopo una nuova installazione: fermare il servizio, rimettere il file del database e la cartella `documents` (in `data\`) al loro posto, riavviare.

**Aggiornamento da una versione precedente**: al primo avvio dopo l'aggiornamento, il backend aggiunge da solo le tabelle e le colonne nuove del database (ponte `create_all`/`add_missing_columns`, vedi `backend/db/init_db.py`) — nessuna azione manuale sul database, a meno che tu non stia usando `MIGRATIONS=alembic` (vedi punto 8).

---

## 2. Prima configurazione: il Setup Wizard parte da solo

**Non c'è nulla da configurare a mano prima del primo avvio.** L'ultima schermata del Setup mostra la casella *"Apri PramaIA-PrivacyManager nel browser"* (spuntata di default): appena il browser si apre su `http://127.0.0.1:8003/`, se il file `.env` non ha ancora `PRAMAIA_JWT_SECRET`/`PORTAL_URL`, l'app mostra automaticamente il **Setup Wizard guidato** invece della schermata di login (`frontend/src/App.tsx` → `GET /api/setup/check`).

Il wizard, in 3 passaggi, configura:
- `PRAMAIA_JWT_SECRET` (sincronizzato con il Portal, manuale o recuperato automaticamente);
- `APP_CLIENT_SECRET` (generato o recuperato dal Portal);
- `PORTAL_URL` / `PORTAL_API_URL`.

Se preferisci configurare **a mano** invece di usare il wizard, nella cartella di installazione:
1. Copia `.env.example` in `.env`;
2. Compila `PRAMAIA_JWT_SECRET` e `PORTAL_URL` (deve corrispondere al Portal);
3. Se l'app non è ancora registrata sul Portal, eseguila dal sorgente prima del deploy: `python register_to_portal.py`;
4. Riavvia il servizio (sezione 3).

### Registrazione sul Portal per l'installazione (porta 8003)

Nel pacchetto installato interfaccia web e API stanno sulla **stessa porta 8003** (nessun frontend separato su 3003, quello è solo per lo sviluppo). Nella scheda dell'app sul Portal (Amministrazione → Applicazioni) imposta:

| Campo | Valore |
|-------|--------|
| URL | `http://<indirizzo-server>:8003` (raggiungibile sia dagli utenti sia dal Portal) |
| Health endpoint | `/api/health` (non `/health/live`: entrambi esistono, ma questo è quello registrato per convenzione con le altre app PramaIA) |

Se l'app risulta *offline* sul Portal, verifica per prima cosa che l'URL registrato sia quello su cui il servizio ascolta davvero e che la porta non sia condivisa con un'altra app.

---

## 3. Gestione servizio Windows

```powershell
# Nella directory di installazione:
.\service.ps1 install    # installa il servizio (se non fatto dal wizard di Inno Setup)
.\service.ps1 start
.\service.ps1 stop
.\service.ps1 status
.\service.ps1 uninstall
```

Oppure `services.msc` → cerca "PramaIA-PrivacyManager".

---

## 4. Errore ricorrente: porta 8003 già in uso (`WinError 10048`)

Stessa causa tipica del doppio avvio già vista su altri servizi PramaIA: il servizio Windows ha già il bind sulla porta, e un secondo avvio manuale (`start-service.cmd`) prova a fare bind di nuovo.

**Verifica prima se il servizio è già sano:**

```powershell
Get-Service PramaIA-PrivacyManager
Invoke-RestMethod http://127.0.0.1:8003/api/health
```

Se risponde `{"status":"ok",...}` il servizio va bene: chiudi la seconda console e ignora l'errore.

**Se non risponde**, ripulisci e riavvia solo il servizio:

```powershell
Stop-Service PramaIA-PrivacyManager -Force -ErrorAction SilentlyContinue
taskkill /F /IM PramaIA-PrivacyManager.exe 2>$null
Start-Sleep -Seconds 2
Start-Service PramaIA-PrivacyManager
Start-Sleep -Seconds 3
Invoke-RestMethod http://127.0.0.1:8003/api/health
```

**Checklist per non ripetere l'errore:**
- [ ] Spunta **solo** "Installa come servizio Windows" nel wizard
- [ ] Non avviare a mano `start-service.cmd` se il servizio è già Running
- [ ] Non aprire una seconda console "per prova" con il servizio attivo

---

## 5. Verifica post-install

```powershell
Get-Service PramaIA-PrivacyManager
# Status: Running

Invoke-RestMethod http://127.0.0.1:8003/api/health
# {"status":"ok","service":"PramaIA-PrivacyManager"}

# Interfaccia web (mostra il Setup Wizard al primo avvio)
start http://127.0.0.1:8003/

# Log servizio (se configurati da nssm)
#   {app}\logs\service.log
#   {app}\logs\service-error.log
#   {app}\logs\privacymanager_service.log   (log applicativo, vedi run_service.py)
```

---

## 6. Struttura directory post-installazione

```
C:\Program Files\PramaIA\PrivacyManager\
├── PramaIA-PrivacyManager.exe   # Eseguibile
├── _internal\                    # Librerie Python e frontend compilato (non modificare)
├── nssm.exe                      # Service manager
├── start-service.cmd             # Avvio manuale (solo se NON servizio)
├── service.ps1                   # Installa/avvia/ferma il servizio Windows
├── .env                          # Configurazione (creata dal Setup Wizard, o da .env.example)
├── logs\
│   ├── service.log
│   ├── service-error.log
│   └── privacymanager_service.log
├── data\                          # Database SQLite (PramaIA-PrivacyManager.db) e documenti caricati
└── backups\                       # Backup pianificati/manuali (vedi RUNBOOK_RIPRISTINO.md)
```

Il frontend React è incluso nell'eseguibile stesso (`_internal\frontend\build`) e servito
dal backend su `http://127.0.0.1:8003/` — un solo processo, una sola porta.

**Persistenza:** `data\` e `backups\` non vengono mai rimossi dal disinstallatore (`uninsneveruninstall`).
Un aggiornamento dell'installer li lascia intatti; una disinstallazione completa va fatta cancellando
la cartella a mano, se davvero si vuole eliminare tutto.

---

## 7. Riepilogo errori tipici

| Errore | Causa | Rimedio |
|--------|--------|---------|
| File in uso / accesso negato su `nssm.exe` o `.exe` | Servizio ancora running durante la copia | `PrepareToInstall` lo gestisce da solo; altrimenti stop manuale admin **prima** del setup |
| `WinError 10048` porta 8003 | Due bind sulla stessa porta | Un solo avvio (servizio **o** cmd); health check; kill + `Start-Service` |
| Il browser si apre ma mostra una pagina bianca o un errore di connessione | Il servizio non è ancora partito (avvio lento la prima volta: crea il database) | Attendi qualche secondo e ricarica; controlla `logs\service-error.log` |
| Il wizard non compare, va direttamente al login | `.env` esiste già con `PRAMAIA_JWT_SECRET`/`PORTAL_URL` valorizzati (es. da un'installazione precedente non rimossa) | Atteso: significa che l'app è già configurata. Per riconfigurare, svuota quei due campi in `.env` e riavvia |
| Login non funziona dopo il wizard | Portal non raggiungibile dal server, o `PORTAL_URL` errato | Verifica `PORTAL_URL` in `.env` e che il Portal risponda da questa macchina |
| Servizio non parte | `.env` mancante o incompleto | Controlla `logs\service-error.log`; se manca del tutto va bene: il wizard lo crea al primo accesso web |
| Da questa stessa macchina (`http://127.0.0.1:8003/` o `http://localhost:8003/`) funziona, ma da un'altra macchina della rete no | Windows Firewall: un servizio non interattivo (via NSSM) non fa mai comparire il prompt "Consenti l'accesso" che vedrebbe un'app grafica, quindi senza una regola esplicita le connessioni in ingresso da altre macchine restano bloccate in silenzio | L'installer crea da sé la regola (`netsh advfirewall firewall add rule name="PramaIA-PrivacyManager" …`). Se manca (installazioni fatte con una versione precedente dell'installer), crearla a mano, come amministratore: `New-NetFirewallRule -DisplayName "PramaIA-PrivacyManager" -Direction Inbound -Action Allow -Program "C:\Program Files\PramaIA\PrivacyManager\PramaIA-PrivacyManager.exe" -Profile Any` |

---

## 8. Migrazioni del database (avanzato, opzionale)

Di default il backend usa il ponte `create_all` + aggiunta automatica delle colonne mancanti
(`MIGRATIONS` vuoto in `.env`): va bene per la stragrande maggioranza delle installazioni e non
richiede nulla. Con `MIGRATIONS=alembic` il backend esegue le revisioni Alembic vere e proprie
all'avvio (`backend/migrations/`, incluse nell'eseguibile). **Non testato nel pacchetto compilato**:
se lo usi, verifica con un'installazione di prova prima di distribuire in produzione.

---

*Documento operativo installer, adattato dallo stesso schema di `C:\PramaIA-SysAdmin2\installer\INSTALLER_README.md`.*
