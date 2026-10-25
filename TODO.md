# TODO — PrivacyPA

Riferimento: `PrivacyPA_Specifica_Funzionale_Tecnica_v1.0.md` (i numeri `§` sono i suoi capitoli).
Stato al 2026-09-20: tutte le voci dell'MVP (§5) sono realizzate, salvo l'invio di email/notifiche esterne.
Realizzati anche informative, flussi dei dati, conservazione, filiera, motore di regole, riferimenti normativi,
azioni correttive, data breach, richieste degli interessati, audit strutturati, formazione, autorizzati al
trattamento, pareri del DPO, DPO workspace, relazione annuale, privacy by design (nuovi progetti) e annotazioni trasversali (tag, campi custom, commenti) riservatezza (livelli, filigrana, step-up, blocco dell'export massivo), ricerca globale (anche per tag), query builder, webhook firmati (vedi `WEBHOOKS.md`), scadenze relative per ente, SBOM, test di autorizzazione e di isolamento tra enti, backup con verifica di ripristino (vedi `RUNBOOK_RIPRISTINO.md`).

## Rimandato per scelta

### Reminder ed email con scheduler (§36, §74, §156, AC-PROC-003)
Sarà coperto da un prodotto esterno già quasi pronto: PrivacyPA non invia email e non ha uno scheduler.
Punti di aggancio già disponibili, da usare al posto di leggere il database:

- **Outbox** (§130): `GET /api/privacypa/outbox` restituisce gli eventi non consegnati, con `payload.data` che porta
  le date utili (`due_at`, `due_date`, `expiry_date`, `end_date`, `renewal_date`, `next_review_date`, `valid_to`,
  `aware_at`, `status`). `POST /outbox/ack` conferma la consegna, `POST /outbox/{id}/retry` segnala un errore,
  `DELETE /outbox/processed` ripulisce. Consegna "almeno una volta": l'id evento è la chiave di idempotenza. Solo
  amministratori. I payload non contengono dati descrittivi né personali (richiedenti, descrizioni di violazioni).
- **Rivalutazione delle regole**: `POST /api/privacypa/findings/evaluate` (idempotente) da chiamare periodicamente;
  apre le segnalazioni nuove e chiude quelle risolte, pubblicando `finding.opened` / `finding.resolved` sull'outbox.
  Con le regole per scadenze delle richieste (`DSR_*`), azioni correttive e violazioni, è il modo per ricevere i
  promemoria senza logica nel prodotto esterno.
- **Notifiche in-app**: `GET /api/privacypa/notifications` resta il feed per il campanello dell'interfaccia.

Da fare quando si integra il prodotto: registrare il suo utente di servizio come amministratore dell'ente (oggi
l'outbox richiede un ruolo amministrativo) e decidere il formato dei messaggi. Le scadenze relative (§156) sono configurabili:
la prossima revisione di trattamenti e DPIA si calcola dall'approvazione (`/policy/deadlines`), gli anticipi dei promemoria
(`promemoria = scadenza - 90 giorni`) sono i parametri delle regole di conformità. In alternativa a leggere l'outbox, il prodotto
esterno può ricevere gli eventi come webhook firmati (`WEBHOOKS.md`).

## Infrastruttura

- **PostgreSQL (ADR-002)**: il default resta SQLite. Alembic e l'applicazione sono pronti a usare l'URL
  `postgresql+asyncpg://…` (le migrazioni usano il driver sincrono equivalente, da installare), ma nulla è stato
  provato su PostgreSQL: `JSON`, indici univoci con colonne NULL (`active_key`), `version_id_col` e la baseline vanno
  verificati su un'istanza reale.
- **Alembic (§59, §90)**: baseline e ponte per i database preesistenti sono pronti e testati su SQLite
  (`backend/db/migrate.py`, `MIGRATIONS=alembic`). Restano da decidere il passaggio del default e il processo di
  rilascio; per ora il ponte `add_missing_columns` è ancora il percorso predefinito. Ogni modifica ai modelli richiede
  una nuova revisione: un test fallisce se schema migrato e modelli divergono.
- **OIDC (§9, §55)**: validazione dei token dell'IdP (JWKS, algoritmi asimmetrici, issuer, audience, tenant da
  claim) pronta e testata con chiavi generate in locale; non provata con un IdP reale. Manca il flusso interattivo di
  login (authorization code + PKCE) e lo step-up per le azioni sensibili (§136): l'interfaccia usa ancora il Portal.
- **Idempotency-Key (§131)** attiva sulle POST JSON (middleware); non copre i caricamenti multipart.
- **Step-up MFA senza flusso di riautenticazione**: l'interfaccia mostra il messaggio ma non rimanda al Portal con `acr_values`/`max_age`.
- **Prova di ripristino su un altro server e RPO/RTO reali (§57)**: il runbook c'è, la prova non è mai stata fatta; l'RTO nel runbook è ancora da compilare. Rate limit distribuito (oggi in memoria, per processo).
- **Allineamento delle dipendenze**: l'ambiente in cui si sono eseguiti i test non coincide con `requirements.txt` (SBOM: `pytest` 8.3.3 invece di 8.0.0, `openpyxl` 3.1.2 invece di 3.1.5,
  `python-multipart` 0.0.12 invece di 0.0.9, `uvicorn` 0.32.0 invece di 0.30.0, `pytest-asyncio` 0.24.0, `pydantic-settings` 2.5.2). L'immagine Docker installa le versioni fissate: la suite
  non è mai stata eseguita con quelle. Da fare: una CI che installi `requirements.txt` e lanci i test.
- **Scanner antimalware reale**: il protocollo clamd è provato solo con un server finto; provare con ClamAV vero.
- **Docker**: il `Dockerfile` non è stato costruito né avviato.

## Installer Windows (§ nessuna nella specifica; richiesto separatamente)

- **Fatto**: `installer/` (Inno Setup + NSSM + PyInstaller, stesso schema di `C:\PramaIA-SysAdmin2`): `installer.iss`, `build-release.ps1`,
  `service.ps1`/`start-service.ps1`/`start-service.cmd`, `INSTALLER_README.md`, `nssm.exe` copiato da SysAdmin2. `run_service.py` alla radice
  (entry point PyInstaller: logging su file prima di importare `backend.main`, poi `uvicorn.run` sulla porta `BACKEND_PORT`, default 8003).
  `backend/main.py` serve il build React (`frontend/build`) sulla stessa porta dell'API se la cartella esiste (PyInstaller la include via
  `--add-data`), con fallback a `index.html` per il routing lato client; inattivo in sviluppo/test (la cartella non esiste). Al termine
  dell'installazione Inno Setup apre il browser sull'app: se `.env` non ha ancora `PRAMAIA_JWT_SECRET`/`PORTAL_URL` (caso normale al primo
  install), parte da solo il Setup Wizard esistente — nessun codice nuovo per questo, solo la sequenza dell'installer che ci porta davanti.
  Corretta un'incoerenza preesistente in `.env.template` (`BACKEND_PORT=7003`, mai realmente usato da Docker/`start-all.ps1`, che usano sempre
  8003): ora è 8003 ovunque.
- **Nessun database esterno**: a differenza di SysAdmin2 (PostgreSQL), PrivacyPA usa solo SQLite anche nel pacchetto installato — coerente con
  `services/backup_service.py`, che rifiuta esplicitamente PostgreSQL.
- **Costruito e provato**: `installer\build-release.ps1` eseguito per davvero (PyInstaller 6.22.3, npm, Inno Setup 6, tutti presenti sulla macchina
  di sviluppo). Generato `installer/installer_output/PramaIA-PrivacyManager-Setup-1.0.0.exe` (27 MB). L'eseguibile compilato (`release/PramaIA-PrivacyManager.exe`,
  non l'installer) è stato avviato in una copia isolata (fuori dal repository) e verificato: si avvia, crea da solo database SQLite e cartelle,
  `GET /api/health` → 200, `GET /` serve la pagina React con gli asset `/static/js/...`, `GET /api/setup/check` → `is_configured: false` (il wizard
  parte davvero), le rotte sconosciute non-API tornano `index.html` mentre `/api/*` sconosciute restano 404, il pianificatore dei backup si avvia,
  nessun errore nel log applicativo.
- **Non verificato**: il file `.exe` dell'**installer** (quello con Inno Setup) non è mai stato eseguito, perché richiede permessi di amministratore
  e installa un vero servizio Windows (via NSSM) sulla macchina — non fatto senza conferma esplicita, essendo un'azione invasiva su questo ambiente
  di sviluppo. Non testati: `MIGRATIONS=alembic` dentro l'eseguibile compilato (i percorsi di Alembic potrebbero non risolversi sotto `sys._MEIPASS`;
  il ponte `create_all`, verificato, resta il percorso predefinito), l'installazione su un Windows pulito, il login reale col Portal dal servizio
  installato, la disinstallazione.
- **Difetto reale trovato dopo un'installazione vera (v1.0.0)**: l'app installata come servizio Windows era raggiungibile in locale (`127.0.0.1:8003`)
  ma non da altre macchine della rete. Causa: un servizio non interattivo (via NSSM) non fa mai comparire il prompt "Consenti l'accesso" di Windows,
  quindi senza una regola esplicita il firewall blocca in silenzio le connessioni in ingresso. **Corretto in v1.0.1**: `installer.iss` crea la regola
  (`netsh advfirewall firewall add rule … profile=any`) dopo l'avvio del servizio, e la rimuove alla disinstallazione. Rimedio manuale per chi ha
  installato la v1.0.0: `New-NetFirewallRule -DisplayName "PramaIA-PrivacyManager" -Direction Inbound -Action Allow -Program "<percorso>\PramaIA-PrivacyManager.exe" -Profile Any` (richiede admin).
- **Secondo difetto trovato mentre correggevo il primo**: `build-release.ps1 -SkipBackend` cancellava comunque `release\` al passo di pulizia (sempre
  incondizionata) senza poi ricreare l'eseguibile: un installer "veloce" per una modifica solo a `installer.iss` risultava **senza payload**, nessun
  errore visibile durante la build. Scoperto perché il pacchetto `1.0.1` generato così pesava uguale ma conteneva `release\` vuota di `.exe`/`_internal`
  — controllato prima di consegnarlo, non distribuito. Corretto: la pulizia non cancella più `release\` se `-SkipFrontend`/`-SkipBackend` è attivo, e
  un controllo esplicito blocca la build se manca l'eseguibile atteso.
- **Verificato per davvero, questa volta con un'installazione reale** (non solo l'eseguibile nudo): `PramaIA-PrivacyManager-Setup-1.0.1.exe` installato,
  servizio Windows `PramaIA-PrivacyManager` creato e avviato da NSSM, raggiungibile su `127.0.0.1:8003` e su un'altra interfaccia di rete della stessa
  macchina, log puliti, database SQLite creato al primo avvio, `GET /api/setup/check` conferma che il wizard partirebbe. Non ancora verificato:
  l'accesso da una macchina fisicamente diversa (non disponibile in questo ambiente) e la disinstallazione completa.
- **Terzo difetto, questa volta un vero difetto applicativo (non solo dell'installer) trovato dall'utente**: dopo il login dal Portal (redirect con
  `?token=`), pagina completamente bianca. Causa: `SecurityHeadersMiddleware` (`backend/middleware/security.py`, §55.6) impostava su **ogni** risposta,
  frontend incluso, `Content-Security-Policy: default-src 'none'` — corretto quando questo backend era solo un'API, ma il pacchetto installer serve
  ora anche il frontend dalla stessa porta (vedi sopra), e quella CSP blocca il proprio script, il proprio foglio di stile, i font di Google e persino
  le chiamate dell'app verso sé stessa. **Non è mai emerso nei test**: nessun test copriva la CSP delle rotte non-API (semplicemente non esistevano
  prima dell'installer). **Corretto in v1.0.2**: CSP separata per `/api/*` e `/health/*` (invariata, `default-src 'none'`) e per il resto (`script-src
  'self'`, `style-src 'self' 'unsafe-inline'` + Google Fonts, `connect-src 'self'`, `frame-ancestors 'none'`); nuovo test
  `test_frontend_csp_allows_its_own_script_and_style_but_not_the_api_wildcard` in `test_privacypa_hardening.py` (10/10 passati). Diagnosticato dalla
  console del browser dell'utente (screenshot con gli errori CSP espliciti) — senza quello sarebbe stato molto più lento da trovare da qui.
- **Resta**: icona applicativa (oggi nessuna, Inno Setup userebbe l'icona di default dell'eseguibile); verificare il login reale col Portal dal
  servizio installato.

## Moduli funzionali non ancora realizzati

- **Ricerca nel contenuto dei documenti (§39)** e interrogazioni salvate/condivise (§40): oggi si cerca su codice, titolo, descrizione e tag.
- **Integrazioni con protocollo (§69)** e altre interoperabilità (directory, HR, SIEM, procurement), IA (§71-§72).
- **Riferimenti normativi**: il collegamento ai trattamenti non ha `purpose_id` (le finalità si ricreano a ogni
  modifica del trattamento e cambiano id): oggi il riferimento vale per l'intero trattamento.
- **Motore di regole**: mancano le checklist per altri eventi (es. cessazione di un fornitore, §112); la UI delle
  regole non permette di scrivere condizioni nuove, solo di configurare quelle esistenti (attiva/disattiva, gravità,
  parametri). `NOTICE_REVIEW_REQUIRED` confronta l'audit del trattamento: le modifiche fatte prima della correzione
  dell'audit delle relazioni (`update_activity` registrava le relazioni precedenti) non sono rilevabili.

## Limiti noti dei moduli persone e DPO (§42-§45)

- **Formazione**: nessun quiz né autoattestazione: il completamento lo registra chi gestisce la formazione. L'attestato
  (`certificate_document_id`) e la sessione frequentata si indicano solo via API, non dal modulo dell'interfaccia. Le
  sessioni sono un calendario del corso, senza elenco dei partecipanti. L'assegnazione automatica usa ruoli e strutture
  delle assegnazioni di ruolo attive; non c'è un'anagrafica del personale.
- **Autorizzati**: le persone sono identificate dall'identificativo utente opaco (nessuna anagrafica). Un'autorizzazione
  solo su un sistema segue la struttura responsabile del sistema. Il cambio di struttura del trattamento (§45) genera una
  verifica (`AUTHORIZATION_REVIEW_UNIT_CHANGED`, confrontando l'audit del trattamento), non una checklist di task.
- **Pareri del DPO**: sono registrati a parte e immutabili, ma non condizionano il flusso di approvazione (nessun blocco
  se manca il parere) e il campo `dpo_opinion` della DPIA resta separato. Solo un utente con ruolo DPO assegnato sulla
  struttura li emette: gli amministratori del Portal non li emettono al posto del DPO.
- **Relazione annuale**: i dati si calcolano alla lettura finché è in bozza e si congelano con la finalizzazione; la
  sezione "criticità" riflette lo stato delle verifiche al momento della generazione, non dell'anno. Solo esportazione
  in Word (niente PDF). Serve visibilità sull'intero ente (aggrega tutto). Le statistiche di risposta alle richieste
  usano l'anno di ricezione.

## Limiti noti della riservatezza (§134-§136)

- **Dove si applica**: i livelli `RESTRICTED` e `HIGHLY_RESTRICTED` valgono per documenti, violazioni, DPIA e progetti (elenchi, dettaglio,
  ricerca globale, DPO workspace, e tag/commenti/campi dell'elemento). Trattamenti e sistemi rifiutano `HIGHLY_RESTRICTED`; `PUBLIC` e
  `CONFIDENTIAL` restano etichette senza regole. Non filtrati: approvazioni, notifiche e segnalazioni (mostrano codici e titoli),
  contenuto dell'audit trail (quello di una DPIA riporta tutto il testo: lo leggono i ruoli con `audit.view`), conteggi del cruscotto.
- **Regole**: i ruoli si valutano a qualsiasi scope (l'assegnazione di ruolo altrove basta), lo scope per struttura resta un controllo a
  parte. L'autore (e il responsabile del progetto) legge sempre ciò che ha creato; non può però assegnare un livello che non
  leggerebbe per ruolo. Una modifica che omette il livello lo lascia invariato.
- **Filigrana**: solo PDF e Word (.docx); il file archiviato non cambia e l'audit conserva lo sha256. Immagini, Excel e altri
  formati non si marcano (con «obbligatoria» non si scaricano). Non c'è un controllo separato per la stampa. Il testo è ASCII.
- **Step-up (§135, §136)**: il server controlla `amr`/`acr` e `auth_time` (o `iat`) del token, ma non è stato provato con un Portal o un
  IdP reale che li emetta dopo una vera MFA. Attivabile per ente: download dei livelli riservati e azioni amministrative (assegnare
  SUPER_ADMIN, export completo, modifica delle regole di conservazione). Non coperti: ripristino dei backup (route del template) e
  modifica dell'IdP (configurata via ambiente, senza interfaccia).
- **Tetto ai download**: conta gli eventi di audit dell'ultima ora per utente e livello (finestra mobile); non sostituisce un rate limit
  distribuito. **Export completo**: gli elementi `HIGHLY_RESTRICTED` (default) escono come righe strutturali senza contenuto, con file,
  commenti, valori custom e righe di audit collegati; approvazioni e outbox non sono ridotti.

## Limiti noti del backup e del ripristino (§57)

- **Il backup del template non funzionava**: router e pianificatore passavano una sessione asincrona a un servizio che usava l'API sincrona (`db.query` su `AsyncSession`) e il
  pianificatore eseguiva SQL su tabelle inesistenti; `GET /api/backup/logs` era intercettata da `/{filename}`. Nessun test lo notava. Riscritti in modo asincrono
  (`backup_service.py`, `backup_scheduler.py`, `backup_router.py`), con 17 test in `test_backup_dr.py`.
- **Cosa fa ora**: backup del database + archivio dei documenti con manifest; cifratura opzionale AES-256-GCM a blocchi (`BACKUP_ENCRYPTION_KEY`); copia esterna verificata
  per checksum (`BACKUP_OFFSITE_DIR`); **verifica di ripristino** su copia temporanea (decifratura, integrità, revisione dello schema, dati, coerenza documenti/database),
  eseguita a ogni backup pianificato e prima di ogni ripristino; ripristino atomico con backup di sicurezza e rimozione dei `-wal`/`-shm`; ripristino dei documenti che non
  sovrascrive mai; retention che elimina insieme database, archivio e copia esterna.
- **Limiti**: solo SQLite (con PostgreSQL il servizio lo dichiara: `pg_dump`); backup giornaliero, nessun ripristino a un istante; il ripristino richiede il riavvio e non c'è
  modalità di manutenzione; nessun blocco distribuito tra istanze (`BACKUP_SCHEDULER=false` su quelle in più); nessun avviso via email (errori nel registro e nel log ERROR);
  chi perde la passphrase perde i backup cifrati; i segreti dell'applicazione non sono nel backup. Non provato: ripristino su un altro server, database di dimensioni reali
  (la cifratura è a blocchi da 1 MiB, ma la verifica copia il file in una cartella temporanea: serve spazio pari al backup), Windows con antivirus che blocca il file durante lo scambio.
- L'interfaccia (Impostazioni → Backup) ha il pulsante «Verify» e le etichette «Encrypted»/«+ documents»; non ha ancora il pulsante per ripristinare i documenti (solo API) né traduzioni
  (la sezione è in inglese, come nel template).

## Connettore VideoPA: lettura ed eventi (W1, W2, W3)

- **Fatto**: ruolo `INTEGRATION` (vede solo `integration.*`, mai gli altri moduli; `EVERYONE` lo esclude e i permessi non sono modificabili per ente), `GET /integrations/ping`, snapshot per trattamento con ETag/304 e elenco con `updated_since`, pagine da 200, parametro d'ente `retention_category` (`GET/PUT /policy/integrations`, migrazione 0007), conservazione a calendario, DPIA riservata come `{"restricted": true}`. Contratto e procedura in `INTEGRAZIONE_VIDEOPA.md`; 20 test in `test_privacypa_integrations.py`.
- **W3 fatto**: `POST /integrations/events` (fino a 100 eventi, esito per evento ACCEPTED/DUPLICATE/UNMATCHED/REJECTED), tabella `privacypa_external_events` (migrazione 0008), regola `EXTERNAL_CHANGE_REVIEW_PENDING` (una segnalazione per trattamento+tipo con contatore, gravità dal tipo di evento) e task alla struttura + referente privacy, archiviazione con nota (`externalevent.edit`), elenco (`externalevent.view`), conservazione 24 mesi configurabile, audit e outbox `external_event.received`. 15 test in `test_privacypa_external_events.py`.
- **Decisioni prese**: A (categoria configurata per ente), B (liste + DPIA di riferimento), C (ruolo `INTEGRATION`), D (task a struttura + referente), E (VideoPA spinge), F (riservata senza dettagli).
- **Limiti**: `updated_since` non vede la rimozione di un collegamento se non tocca il trattamento (l'ETag sì); l'elenco calcola tutti gli snapshot visibili e poi filtra; la categoria delle riprese si imposta solo via API; `AMBIGUOUS` se due regole diverse condividono la categoria; non provato con un JWT del Portal reale né con un client VideoPA; PostgreSQL non provato.
- **Deviazione dalla bozza**: il ruolo non legge i moduli (trattamenti, DPIA…), solo lo snapshot: meno superficie, nessun bisogno ulteriore.
- **Limiti W3**: una revisione del trattamento chiude la segnalazione ma non archivia gli eventi (restano PENDING fino ai 24 mesi); chiusura per revisione solo se `last_review_date` è successiva al giorno dell'evento; i `UNMATCHED` non si possono ancora smistare (solo elenco e archivio); gravità per tipo fissa nel codice (non configurabile per ente, diversamente dalla bozza); tipi ammessi: elenco chiuso (ANPR assunto come `AnprEnabled`, da confermare con VideoPA); nessuna schermata; ogni ricezione rivaluta tutte le regole dell'ente.
- **Dati di prova (W5, in parte)**: `seed_demo_data.py` crea l'account `svc-videopa`, la categoria delle riprese, cinque trattamenti di videosorveglianza con situazioni diverse ed eventi già ricevuti (vedi `INTEGRAZIONE_VIDEOPA.md`, sezione 8); test `test_demo_seed_runs_and_prepares_the_videopa_scenarios`. Nel farlo è emerso che il seed **non girava più** dopo le validazioni del §141 (mancava una categoria di dati sulla «Videosorveglianza urbana»): corretto. Non è incluso il JWT dell'account di servizio.
- **Resta**: W4 (eventi webhook `dpia.status_changed`, `notice.changed`, `retention.changed`), W5 (ambiente di prova con dati d'esempio), W6 (vista DPO), schermata per la categoria delle riprese.

## Limiti noti di SBOM e dipendenze (§137, §138)

- **Generazione**: `python scripts/generate_sbom.py --version X.Y.Z --out sbom` produce CycloneDX 1.5 e SPDX 2.3 (JSON); un amministratore li ottiene anche da
  `GET /api/privacypa/export/sbom?format=cyclonedx|spdx` (registrato in audit). Va eseguita a ogni release: non è agganciata a una CI, che non esiste.
- **Cosa contiene**: pacchetti Python (requirements e transitive, versione **installata**, con la versione fissata come proprietà se diversa) e, se c'è il lockfile,
  il frontend (npm, con hash SHA-512). L'immagine Docker ha solo il backend, quindi l'endpoint non include il frontend: per quello serve lo script nel repository.
- **Limiti**: le licenze sono quelle dichiarate dai pacchetti e solo se corrispondono a un identificativo SPDX noto (le altre restano «non dichiarate»); nessuna licenza
  «conclusa». I documenti non sono stati validati contro gli schemi ufficiali (non disponibili offline): solo controlli di coerenza (riferimenti, identificativi unici).
  Non c'è analisi delle vulnerabilità (§139), né monitoraggio CVE, né revisione delle dipendenze (§138).

## Limiti noti delle scadenze relative (§156)

- **Cosa è configurabile per ente**: mesi tra approvazione e prossima revisione di trattamenti e DPIA (12 di default, 0 = spento), termini delle richieste degli interessati.
  I mesi sono di calendario (31 gennaio + 1 mese = 28/29 febbraio; 12 mesi non sono 365 giorni).
- **Quando si applica**: solo all'approvazione, se non c'è già una data futura (una data inserita a mano non si sposta); dopo una revisione scaduta la nuova approvazione la rinnova.
  Non si ricalcola cambiando la configurazione. Altre date (revisione di trasferimenti, verifica dei responsabili, informative) restano manuali; per le informative e i responsabili
  ci sono regole con orizzonti configurabili nei parametri delle regole, non un calcolo dall'approvazione.

## Sicurezza: autorizzazione e isolamento (§141, §142)

- **Test**: `test_privacypa_isolation_matrix.py` esamina tutte le rotte dell'applicazione vera (ogni rotta richiede autenticazione; ogni scrittura ha un controllo di permesso), prova ogni
  scrittura con un utente di sola lettura e uno senza ruoli (mai 2xx né 5xx), confronta la matrice dei permessi con le rotte per tutti i ruoli su 16 azioni, e prova a leggere,
  modificare, cancellare e collegare (~50 tentativi) record di un altro ente. La ricerca dei controlli mancanti è per dipendenza o per testo del gestore (`INLINE_CHECKS`): un controllo
  fatto altrove sfugge e va aggiunto all'elenco.
- **Difetti trovati e corretti**: `GET /roles/catalog` non richiedeva autenticazione; in creazione e modifica si potevano indicare id di un altro ente per struttura
  (trattamenti, sistemi, task), trattamento (DPIA), fornitore (sistemi, trasferimenti), documento (atti art. 28, trasferimenti, misure), collegamenti di documenti e
  trasferimenti, rischi e misure (`privacypa_refs.py`; ora 400 «non trovato»). Le voci di catalogo create da un altro ente non si usano (le globali sì).
- **Limiti**: i collegamenti con un tipo di elemento non riconosciuto non si verificano (non risolvono dati); le voci di catalogo inesistenti restano accettate (i dati di partenza le usano);
  la matrice per struttura (ruolo su una sola struttura) è coperta dai test dei singoli moduli, non da un test parametrizzato; nessun penetration test (§140).

## Limiti noti dei webhook (§70)

- **Gli eventi di scadenza dipendono dalle verifiche**: `task.overdue`, `dpia.review_due`, `document.expiring` e `processor.expiring` partono
  quando `POST /findings/evaluate` apre il finding (nuove regole `TASK_OVERDUE`, `DPIA_REVIEW_DUE`, `DOCUMENT_EXPIRING`, attive di default e
  disattivabili dalla pagina delle regole). Senza chiamata periodica (il prodotto di pianificazione esterno) non partono. Il reminder ed email
  resta rimandato; i webhook sono il canale con cui quel prodotto può essere avvisato invece di leggere l'outbox.
- **Worker in-process**: un ciclo asyncio (`WEBHOOK_WORKER`, ogni 5 s) nello stesso processo dell'API. Con più istanze non si duplicano le consegne
  (presa in carico condizionata) ma ogni istanza fa lo stesso lavoro. Un processo fermo non consegna: le consegne restano in coda e ripartono all'avvio.
  Non provato con PostgreSQL né con più processi reali.
- **Destinazione**: il controllo dell'indirizzo (solo pubblici, HTTPS) si ripete a ogni invio ma tra la risoluzione DNS e la connessione resta la finestra del
  DNS rebinding; la connessione non è fissata all'IP verificato. Per reti interne servono `WEBHOOK_ALLOWED_HOSTS` o `WEBHOOK_ALLOW_PRIVATE_NETWORKS`.
  Non provato contro un server reale: i test usano un trasporto finto.
- **Riservatezza**: gli eventi di violazioni, DPIA e documenti riservati partono comunque (solo id, stato e date): chi riceve sa che esistono. Non c'è un
  filtro per livello. Il payload non contiene mai titoli, descrizioni o persone.
- **Segreti**: cifrati con `PRIVACYPA_SECRETS_KEY` (o `PRAMAIA_JWT_SECRET`); se la chiave cambia i segreti vanno rigenerati. Nessuna rotazione con periodo
  di grazia: il segreto vecchio smette di valere subito. Gli endpoint non sono negli export dell'ente (segreti e stato tecnico).
- **Consegne**: ordine non garantito, «almeno una volta»; nessuna sottoscrizione per struttura o filtro sul contenuto; tetto di 20 endpoint per ente;
  lo storico si pulisce a mano (`DELETE /webhooks/deliveries`). I webhook sono anche un'azione amministrativa sensibile per lo step-up MFA (se attivo).
- **Regole nuove**: `TASK_OVERDUE`, `DOCUMENT_EXPIRING` e `DPIA_REVIEW_DUE` compaiono anche nell'elenco delle verifiche e nel cruscotto; l'orizzonte dei documenti
  (30 giorni) è un parametro della regola. Non hanno checklist né azioni automatiche.

## Limiti noti di ricerca globale e query builder (§39-§40)

- **Ricerca globale** (`GET /search`): codice, titolo, descrizione e tag di 10 tipi (trattamenti, DPIA, sistemi, soggetti, contratti,
  informative, violazioni, progetti, azioni, audit); documenti, task, trasferimenti e rischi solo per codice e titolo e senza tag (non sono
  annotabili). Non cerca nel contenuto dei file. Risposta con `counts` e `total` calcolati dopo il filtro di modulo, struttura e riservatezza.
- **Query builder** (`/queries`): stessi 10 tipi; non i documenti, i task, i trasferimenti, i rischi e le richieste degli interessati
  (queste per scelta: portano dati del richiedente). Nessun campo personale (titolari, responsabili, riferimenti a persone).
  Condizioni tutte/almeno una a un solo livello (niente gruppi annidati), nessuna interrogazione salvata. I campi custom non si
  interrogano (i valori sono JSON e il confronto dipende dal database). Gli operatori sono quelli della specifica: niente maggiore/minore
  sui numeri. Il confronto sui testi ignora maiuscole e minuscole, non gli accenti.
- **Visibilità**: SQL per le condizioni, poi filtro per struttura e livello sulle righe lette, prima di contare e impaginare. Legge tutte le
  righe che soddisfano la condizione: va bene per migliaia, da rivedere per decine di migliaia. Esportazione massima 10.000 righe.
  `has_dpia` guarda le DPIA non archiviate del trattamento senza considerarne il livello: chi non può leggere una DPIA altamente
  riservata vede comunque il trattamento «con DPIA».
- **Esportazione** (`query.export`, di default DPO, privacy manager, auditor, manager e amministratori): CSV e Excel (foglio `Informazioni`
  con ente, data, utente e condizioni); non PDF/Word. L'audit riporta campi e operatori ma non i valori. Esclude i livelli con blocco
  dell'export massivo (`X-Excluded-Rows`). Non richiede lo step-up MFA.
- **Interfaccia**: struttura e fornitore compaiono come identificativi, i valori enumerati con il codice originale; non provata in un browser.
  Non provato su PostgreSQL (`lower`, `ilike`, `EXISTS` correlati).

## Limiti noti delle annotazioni (§77-§79)

- **Tipi di elemento**: tag e commenti su trattamenti, DPIA, sistemi, fornitori, contratti, informative, violazioni, richieste,
  azioni correttive, audit e progetti (non su documenti, task, rischi, trasferimenti, formazione, autorizzazioni). I campi
  custom solo su trattamenti, DPIA, sistemi, fornitori, contratti, informative e progetti: mai su violazioni e richieste.
  Massimo 20 campi per tipo di elemento, 20 tag per elemento.
- **Campi custom**: chiave e tipo non cambiano dopo la creazione; un campo con valori si disattiva (il valore resta visibile
  in sola lettura) e non si elimina; un'opzione in uso non si toglie. I campi non sono nell'Art. 30, nei report né
  nell'import da Excel. Un campo obbligatorio non blocca la creazione dell'elemento: lo rileva la verifica
  `CUSTOM_FIELD_REQUIRED_MISSING` e impedisce solo di salvare i campi lasciandolo vuoto.
- **Commenti**: `TEAM` = chi ha il permesso di modifica del modulo sulla struttura dell'elemento; `DPO_ONLY` = autore e
  DPO con ruolo assegnato (gli amministratori del Portal non lo leggono); `PRIVATE` = solo l'autore, nemmeno chi modera.
  Solo l'autore modifica; elimina l'autore o chi modera (governo) ciò che può leggere; l'eliminazione è logica. Audit e outbox
  non riportano il testo dei commenti né i valori dei campi custom, solo chi ha fatto cosa e su quale elemento.
- **Tag**: un tag in uso si disattiva, non si elimina. L'elenco «elementi con questo tag» mostra solo ciò che l'utente vede.
  I tag non compaiono nei report né nell'Art. 30.
- **Interfaccia**: il pulsante Tag/campi/commenti è nelle righe di registro, sistemi, fornitori, DPIA, informative, violazioni,
  richieste, azioni, audit e progetti; contratti, task e rischi non lo hanno.

## Limiti noti del modulo privacy by design (§30)

- **Workflow**: la revisione del DPO e lo screening della DPIA scattano se è dichiarata almeno una caratteristica di rischio
  (IA, profilazione, decisioni automatizzate, dati particolari o giudiziari, minori, monitoraggio sistematico, larga scala);
  il solo uso del cloud non basta. Non è configurabile per ente. Il progetto si modifica solo in proposta; chiedere
  modifiche riporta in proposta e apre un nuovo giro (lo storico resta). Il responsabile del progetto non decide sulle
  revisioni del proprio progetto. La revisione post-implementazione è a 6 mesi dal go-live (non configurabile).
- **Generazione degli elementi**: dalla fase DPIA in poi si creano, in bozza e una sola volta, trattamento, sistema
  (collegato al trattamento), contratto e atto art. 28 (se c'è un fornitore, l'atto solo se è un responsabile),
  informativa (collegata al trattamento) e task. La DPIA nasce con l'azione dedicata (screening prefissato dai flag).
  Non si «aggiornano» gli elementi già generati se il progetto cambia, e le **misure di sicurezza** non sono create:
  c'è un task per definirle. Il fornitore non si genera (deve esistere prima). I collegamenti creati alla generazione
  non producono un evento di audit di relazioni sul trattamento.
- **Permessi**: chi genera deve poter creare ciascun elemento (trattamento, sistema, fornitore/contratto, informativa,
  task). La revisione del DPO richiede un ruolo DPO assegnato, come i pareri.

## Limiti noti del codice esistente

- **PATCH come PUT**: i body `*Upsert` azzerano i campi omessi; i client devono inviare l'oggetto completo.
- **Snapshot di versione del trattamento**: non includono regole di conservazione, flussi, informative e riferimenti
  normativi collegati (sì audit e report).
- **Informative**: il testo è libero (nessun editor ricco); la pubblicazione sul sito dell'ente è solo un indirizzo
  registrato (`published_url`), non un'integrazione. Nessun modello di informativa preinstallato (§47).
- **Flussi dei dati**: gli estremi "interessati" ed "esterno" sono etichette libere, non anagrafiche; il grafo è un
  semplice disegno a tre colonne, senza layout automatico per i casi complessi.
- **Data breach**: il timer delle 72 ore è solo informativo (§26.2). La correzione del momento di conoscenza conserva
  l'originale e l'audit ma non impone un secondo approvatore. I collegamenti sono un elenco di riferimenti, senza
  cronologia dei singoli link.
- **Richieste degli interessati**: la proroga è unica e il calcolo usa mesi di calendario con parametri per ente; la
  logica va validata dall'ente (§158). Il riferimento del richiedente è opaco e non esce dall'API. La creazione dei
  task per gli uffici non ha un modello di approvazione delle risposte.
- **Azioni correttive**: dall'interfaccia si creano solo azioni manuali, da raccomandazione del DPO o da valutazione
  di sicurezza; quelle da audit (e, via API, da violazione, DPIA o trattamento) nascono dal rispettivo modulo o dall'API.
- **Contratti, sub-responsabili e registri condivisi** (asset, fornitori, rischi, trasferimenti) sono visibili a
  tutto l'ente per scelta (§2.1): non hanno scope per struttura.
- **Testi delle regole di conformità** (nome, descrizione, condizione) sono solo in italiano: la lingua dell'interfaccia
  traduce i messaggi dei finding ma non il catalogo delle regole.
- **Documenti e task**: il proprietario del record può sempre modificarlo anche fuori dallo scope di scrittura.
- **Interfaccia**: nessuna pagina è stata provata in un browser; solo `tsc` e il controllo delle traduzioni. Le
  nuove pagine (violazioni, richieste, azioni, audit, riferimenti) usano un componente CRUD generico.
