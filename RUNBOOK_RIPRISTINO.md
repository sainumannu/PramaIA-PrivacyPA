# PrivacyPA — Backup e ripristino (§57)

Documento operativo per chi gestisce il servizio. Vale per l'installazione con **SQLite** (predefinita). Per PostgreSQL vedi la sezione 7.

> **Un backup non testato non è un backup.** Il sistema prova da solo il ripristino di ogni backup pianificato (sezione 3), ma una volta a trimestre va fatta la prova completa su un altro server (sezione 6).

## 1. Cosa si salva

Un backup è un **insieme** di file nella cartella `backups/`:

| File | Contenuto |
|---|---|
| `backup_<manual\|scheduled>_AAAAMMGG_HHMMSS.db` | Copia coerente del database (API di backup di SQLite, sicura mentre l'applicazione lavora) |
| `documents_<…>.zip` | Cartella dei documenti (`data/documents`) con un `manifest.json` di checksum. Solo se ci sono documenti |

Con `BACKUP_ENCRYPTION_KEY` i file hanno il suffisso `.enc` (AES-256-GCM, vedi sezione 5). Con `BACKUP_OFFSITE_DIR` ogni file viene copiato anche là e la copia è verificata per checksum.

**Non è nel backup:** i file `.env` e i segreti (`PRAMAIA_JWT_SECRET`, `PRIVACYPA_SECRETS_KEY`, `BACKUP_ENCRYPTION_KEY`, credenziali IdP), il codice, i log. Vanno conservati a parte (sezione 5).

## 2. Configurazione

| Variabile | Effetto |
|---|---|
| `BACKUP_ENCRYPTION_KEY` | Passphrase di cifratura dei backup. Senza, restano in chiaro (la pagina lo mostra) |
| `BACKUP_OFFSITE_DIR` | Cartella di destinazione della copia esterna (condivisione di rete, altro volume, montaggio verso un altro sito) |
| `BACKUP_SCHEDULER=false` | Spegne il pianificatore su questa istanza (con più istanze, lasciarlo acceso su una sola) |
| `DATABASE_URL` | Deve puntare a un file SQLite (`sqlite:///./PramaIA-PrivacyManager.db`) |

Dalla pagina **Impostazioni → Backup** (solo App Admin): ora giornaliera (UTC) e numero di copie da conservare (1–30). Ogni copia ha con sé il suo archivio dei documenti e la copia esterna, e vengono eliminati insieme.

## 3. Cosa controlla la verifica

Il pianificatore esegue ogni notte **backup → verifica**. La verifica si può lanciare anche a mano (pulsante «Verify» nell'elenco, oppure `POST /api/backup/verify`). Ripete il ripristino in una cartella temporanea, **senza toccare il database in uso**, e controlla:

1. **decifratura** con la passphrase attuale (se cifrato);
2. **integrità** del database (`PRAGMA integrity_check`);
3. **schema**: la revisione Alembic è nota al codice; se è più vecchia, avviso (le migrazioni la portano avanti all'avvio con `MIGRATIONS=alembic`); se è *più recente* del codice o mancano tabelle, errore;
4. **dati**: numero di righe delle tabelle principali (un database vuoto genera un avviso);
5. **documenti** (se c'è l'archivio): archivio integro, checksum di ogni file uguale al manifest, e **ogni documento che il database si aspetta è nell'archivio con lo stesso checksum**.

**Dove guardare l'esito:** `GET /api/backup/logs` (operazioni `backup`, `verify`, `offsite`) e il log dell'applicazione. Un backup creato ma non verificato, una copia esterna fallita o un pianificatore che non parte compaiono a livello **ERROR**: collegare quel livello al monitoraggio (mail/allarme) dell'ente. Se in `logs` non compare una riga `backup` di oggi, il backup di stanotte non è avvenuto.

## 4. Ripristino

Prima di ripristinare, **decidere cosa si vuole**: tornare a un backup vecchio perde tutto ciò che è stato inserito dopo. Annotare data e ora del backup scelto e informare gli utenti.

### 4.1 Database (dall'applicazione, o da API)

1. Scegliere il backup nell'elenco e lanciare **Verify**: se non passa, sceglierne un altro. Il ripristino lo rifiuta comunque.
2. **Restore** (con conferma). Il sistema: verifica il backup → crea un **backup di sicurezza** dello stato attuale (`backup_manual_…`, senza documenti) → sostituisce il file del database in modo atomico, elimina gli eventuali `-wal`/`-shm` residui e chiude le connessioni aperte.
3. **Riavviare l'applicazione.** Se il backup è di una versione precedente, avviare con `MIGRATIONS=alembic`.
4. Controllare: accesso, elenco dei trattamenti, ultima riga dell'audit (deve essere quella attesa), `GET /api/backup/logs` (riga `restore` di successo).

Se qualcosa è andato storto: ripristinare il **backup di sicurezza** indicato nella risposta.

### 4.2 Documenti

Il database ripristinato si aspetta i file al loro posto. **Ripristino documenti** (`POST /api/backup/restore-documents`) estrae l'archivio abbinato nella cartella dei documenti, **senza mai sovrascrivere** un file già presente e diverso: i conflitti sono elencati e vanno risolti a mano (probabilmente sono file caricati dopo il backup che vanno tenuti). I percorsi fuori dalla cartella vengono rifiutati.

### 4.3 Da zero, su un nuovo server (disastro completo)

1. Installare la stessa versione dell'applicazione (immagine Docker con il tag della release) e ricreare la configurazione: `.env` con **gli stessi** `PRAMAIA_JWT_SECRET`, `PRIVACYPA_SECRETS_KEY` (o, in mancanza, dipendono dal JWT secret) e `BACKUP_ENCRYPTION_KEY`. **Con una `PRIVACYPA_SECRETS_KEY` diversa i segreti dei webhook non si leggono più: vanno rigenerati.**
2. Copiare i file del backup scelto (dalla copia esterna) in `backups/`. Non è necessario che il database esista: per la prima volta creare un database vuoto avviando l'applicazione una volta, poi fermarla.
3. Ripristinare database e documenti come in 4.1 e 4.2 (o, senza interfaccia: decifrare a mano con `python -c "from pathlib import Path; from backend.services.backup_crypto import decrypt_file; decrypt_file(Path('backup.db.enc'), Path('PramaIA-PrivacyManager.db'))"` con `BACKUP_ENCRYPTION_KEY` impostata; per i documenti decifrare e decomprimere lo ZIP in `data/documents`).
4. Avviare, controllare come in 4.1, riattivare il pianificatore, rifare subito un backup.

## 5. Custodia della passphrase

- **Chi perde `BACKUP_ENCRYPTION_KEY` perde i backup cifrati**: non esiste un recupero. Conservarla in un posto diverso dal server e dai backup (gestore di segreti dell'ente, busta in cassaforte), con almeno due persone che possono accedervi.
- Cambiarla non rende illeggibili i backup vecchi *per il sistema che li ha già creati*, ma **il sistema usa sempre quella attuale**: per verificare o ripristinare un vecchio backup serve la passphrase con cui fu creato. Tenere l'elenco «passphrase → periodo di validità».
- I backup non contengono i segreti dell'applicazione: vanno custoditi con lo stesso rigore.
- La copia esterna deve avere gli stessi controlli di accesso della sorgente (contiene i dati personali dell'ente, in chiaro se non cifrata).

## 6. Prova di ripristino periodica (una volta a trimestre)

Su un server diverso da quello di produzione, partendo **solo dalla copia esterna**:

- [ ] copiare l'ultimo insieme di backup e la passphrase custodita;
- [ ] ripristinare database e documenti (sezione 4.3);
- [ ] aprire l'applicazione, accedere, aprire 3 trattamenti, 1 DPIA e scaricare 2 documenti di anni diversi;
- [ ] confrontare il numero di trattamenti e documenti con quello di produzione;
- [ ] misurare **quanto tempo è passato** dall'inizio al sistema utilizzabile (è il valore reale di RTO) e **l'età del backup** rispetto al momento del «disastro» simulato (è il valore reale di RPO);
- [ ] annotare data, esito, tempi, persona, problemi e azioni correttive.

**RPO e RTO da definire con l'ente** (§57 chiede di stabilirli in fase di esercizio): con il backup notturno l'RPO massimo è di circa 24 ore; se non basta, aumentare la frequenza (oggi il pianificatore è giornaliero) o affiancare la replica del volume. L'RTO dipende dalla prova sopra: **[da compilare dopo la prima prova]**.

## 7. PostgreSQL

Il servizio di backup integrato **non** funziona con PostgreSQL e lo dichiara (errore esplicito). Con PostgreSQL usare gli strumenti del database: `pg_dump` (o `pg_basebackup` con archiviazione WAL per il ripristino a un istante), cifratura e copia esterna a carico dell'infrastruttura, e la stessa prova periodica della sezione 6. L'archivio dei documenti si può comunque produrre a parte (copia della cartella `data/documents` con checksum). Nulla di PrivacyPA è stato provato su PostgreSQL.

## 8. Limiti noti

- Solo SQLite; backup giornaliero (non continuo); nessun ripristino a un istante intermedio.
- Il ripristino del database dall'applicazione richiede il riavvio; durante il ripristino le richieste in corso possono fallire (non c'è una modalità di manutenzione).
- La verifica controlla struttura e coerenza, non il *significato* dei dati: non sostituisce la prova con una persona (sezione 6).
- Il pianificatore non ha un blocco distribuito: con più istanze, spegnerlo su tutte tranne una (`BACKUP_SCHEDULER=false`).
- La copia esterna è una copia di file: la replica geografica e la retention lunga (§57: «retention multilivello») restano a carico dell'infrastruttura.
- Nessun avviso automatico via email: gli errori sono nel registro e nel log a livello ERROR.
