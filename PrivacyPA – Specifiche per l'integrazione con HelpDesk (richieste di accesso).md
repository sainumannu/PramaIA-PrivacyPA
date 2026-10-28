# PrivacyPA – Specifiche per l'integrazione con HelpDesk (richieste di accesso)

Oct 27, 2026 · @Someone

## Scopo e confini

PrivacyPA deve fornire all'HelpDesk il contesto privacy di una richiesta di accesso, gestire l'approvazione dei casi non standard e tenere il registro degli autorizzati allineato con gli accessi concessi e revocati. Queste specifiche sono rivolte al team PrivacyPA. Il quadro generale e le decisioni sono nel documento *HelpDesk ↔ PrivacyPA – Richieste di accesso: analisi e decisioni*.

| Responsabilità | PrivacyPA | HelpDesk |
| --- | --- | --- |
| Profili di accesso (sistema, dati, operazioni, ambito, requisiti) | Gestisce | Legge |
| Valutazione della richiesta | Calcola e restituisce | Chiede, salva l'esito, lo mostra |
| Decisione ordinaria | — | Operatore ICT con gli estremi del responsabile della banca dati |
| Decisione sui casi non standard | Privacy manager | Attende l'esito |
| Esecuzione tecnica | — | Operatore ICT (manuale) |
| Registro degli autorizzati | Conserva `Authorization` con estremi e ticket di origine | Scrive a provisioning o revoca eseguiti |
| Scadenza degli accessi temporanei | Segnala quelli scaduti e non revocati | Apre in automatico il ticket di revoca |

Vincoli:

- PrivacyPA non gestisce account o credenziali e non decide al posto dell'operatore.
- Non c'è alcun controllo sull'ufficio di appartenenza della persona: i profili non corrispondono sempre agli uffici.
- Si estendono modelli e API esistenti (`/api/privacypa/authorizations`, `privacypa_approvals`, webhook, Idempotency-Key). Non si creano strutture parallele.

## Modello dati

Si estendono le due tabelle esistenti con migrazioni additive. I dati già presenti restano validi con i nuovi campi vuoti.

**`privacypa_authorization_profiles`** (oggi: nome, descrizione):

| Campo nuovo | Tipo | Note |
| --- | --- | --- |
| `code` | string(50), unico per tenant | Es. `OPERATORE_ANAGRAFE`; mostrato nell'HelpDesk |
| `asset_id` | FK `privacypa_assets`, nullable | Sistema o banca dati a cui il profilo si applica |
| `data_category_ids` | JSON | Categorie di dati dal catalogo esistente |
| `allowed_operations` | JSON | Sottoinsieme di `READ`, `CREATE`, `UPDATE`, `DELETE`, `EXPORT`, `PRINT`, `DOWNLOAD`, `ADMINISTER` |
| `allowed_scope_types` | JSON | Sottoinsieme di `ALL_RECORDS`, `ASSIGNED_CASES`, `ORGANIZATIONAL_UNIT`, `GEOGRAPHIC_AREA`, `SUBJECT_SET`, `CUSTOM` |
| `processing_activity_ids` | JSON | Trattamenti collegati |
| `required_training_course_ids` | JSON | Corsi richiesti; la validità usa `validity_months` del corso |
| `max_duration_days` | int, nullable | Durata massima per gli accessi temporanei; vuoto = nessun limite |
| `active` | bool | Un profilo non attivo non può essere richiesto |

Il `row_version` esistente fa da versione del profilo: ogni modifica la incrementa.

**`privacypa_authorizations`** (oggi: utente, trattamento/asset, profilo, validità, revoca, ultima revisione):

| Campo nuovo | Tipo | Note |
| --- | --- | --- |
| `authorized_by_name` | string(255) | Responsabile della banca dati che ha autorizzato (nome e ruolo) |
| `authorization_reference` | string(255) | Protocollo, nota o email |
| `authorization_date` | date | Data dell'autorizzazione |
| `authorization_notes` | text, nullable | Note dell'operatore |
| `operations` | JSON | Operazioni concesse |
| `scope` | JSON | `{type, value}` |
| `profile_version` | int | `row_version` del profilo al momento della concessione |
| `source_module` / `source_ref` | string | `HELPDESK` / numero del ticket |
| `forced` | bool | Approvazione forzata da un Admin ICT con PrivacyPA offline |
| `revocation_reference` | string(255), nullable | Estremi della richiesta di revoca |

Ciò che esiste già resta com'è: `valid_to` contiene la scadenza degli accessi temporanei, `revoked_at` / `revoked_by` / `revoked_reason` la revoca.

**`privacypa_assets`** (responsabile della banca dati):

| Campo nuovo | Tipo | Note |
| --- | --- | --- |
| `data_owner_name` | string(255), nullable | Nome del responsabile della banca dati |
| `data_owner_role` | string(255), nullable | Ruolo, es. "Dirigente Servizi Demografici" |
| `data_owner_user_id` | string(100), nullable | Id utente del Portal, se il responsabile è un utente |

I campi si gestiscono dalla scheda dell'asset. L'HelpDesk li usa per precompilare "Autorizzato da" negli estremi di autorizzazione; l'operatore può correggerli.

## Valutazione

La valutazione è deterministica: un insieme di controlli, ciascuno con un esito, da cui si ricava l'esito complessivo `STANDARD`, `REVIEW_REQUIRED` o `UNKNOWN`. Ogni valutazione viene salvata in una nuova tabella `privacypa_access_assessments` (richiesta ricevuta, controlli, esito, versione del profilo, data), con unicità su `source_ref + request_version`.

**Controlli nell'MVP:**

| Codice | Verifica | Esito se non superato |
| --- | --- | --- |
| `PROFILE_APPROVED` | Il profilo esiste, è attivo ed è associato al sistema richiesto | `FAIL` |
| `OPERATION_ALLOWED` | Le operazioni richieste sono comprese in `allowed_operations` | `FAIL` |
| `SCOPE_ALLOWED` | Il tipo di ambito è in `allowed_scope_types` | `FAIL` |
| `TEMPORARY_ACCESS_POLICY` | Con `max_duration_days` impostato, l'accesso è temporaneo e non lo supera | `FAIL` |
| `TRAINING_VALID` | La persona ha completato i corsi richiesti e sono ancora validi | `WARNING` (la policy del tenant può renderlo `FAIL`) |
| `ALREADY_AUTHORIZED` | Per `GRANT`: esiste già un'`Authorization` con lo stesso profilo, in vigore o con inizio futuro | `WARNING` |
| `PRIVACY_INSTRUCTIONS_VALID` | Presa visione delle istruzioni | `NOT_EXECUTABLE` nell'MVP: manca il modello della presa visione (Release 2) |

**Controlli aggiuntivi** (introdotti nell'implementazione, per spiegare gli esiti `UNKNOWN` e il caso di riduzione dei privilegi):

| Codice | Verifica | Esito se non superato |
| --- | --- | --- |
| `SUBJECT_RESOLVED` | La persona è un utente attivo del Portal e membro attivo dell'ente. Per `REVOKE` basta l'id (`NOT_APPLICABLE`) | `FAIL` ed esito `UNKNOWN`; `NOT_EXECUTABLE` se il Portal non risponde, senza cambiare l'esito |
| `SYSTEM_REGISTERED` | Il sistema è registrato nell'ente | Esito `UNKNOWN` |
| `CURRENT_AUTHORIZATION` | Per `CHANGE` e `REVOKE`: l'`Authorization` indicata esiste, è della stessa persona e non è revocata; per `CHANGE` anche non scaduta | Esito `UNKNOWN` |
| `CHANGE_REDUCES_PRIVILEGES` | Per `CHANGE`: la modifica non allarga operazioni, ambito o durata | Solo `PASS` (esito `STANDARD` senza altri controlli) |

Possibili esiti dei controlli: `PASS`, `FAIL`, `WARNING`, `NOT_APPLICABLE`, `NOT_EXECUTABLE`.

**Regole per l'esito complessivo, in quest'ordine:**

1. Sistema non registrato in PrivacyPA, o persona non risolvibile → `UNKNOWN`.
2. Almeno un `FAIL` → `REVIEW_REQUIRED`.
3. Altrimenti → `STANDARD`. I `WARNING` restano visibili all'operatore ma non cambiano l'esito.

**Per tipo di richiesta:**

- `GRANT`: tutti i controlli.
- `CHANGE`: confronto con l'`Authorization` indicata, in vigore oppure non ancora iniziata (una scaduta va richiesta di nuovo con `GRANT`). Se la richiesta riduce operazioni o ambito → `STANDARD`. Se li aumenta o cambia profilo → controlli come per `GRANT` sul nuovo profilo.
- `REVOKE`: esito `STANDARD` se l'`Authorization` esiste e non è revocata (anche se non ancora iniziata), altrimenti `UNKNOWN`. Una revoca riduce i privilegi e non richiede revisione.

L'esito è un'informazione per l'operatore: PrivacyPA non approva né nega.

## API per l'HelpDesk

Un nuovo router `/api/privacypa/access-governance` per catalogo e valutazioni; per il registro si riusa `/api/privacypa/authorizations` con i campi nuovi. Formato degli errori, tenant (`X-Tenant-ID`), `If-Match` e `Idempotency-Key` sono quelli già in uso in PrivacyPA.

| Metodo | Percorso | Scopo | Note |
| --- | --- | --- | --- |
| GET | `/access-governance/assets` | Sistemi selezionabili nel modulo dell'HelpDesk | Solo asset con almeno un profilo attivo; include il responsabile della banca dati come oggetto `data_owner {name, role, user_id}` |
| GET | `/access-governance/profiles?asset_id=` | Profili di un sistema | Codice, nome, operazioni e ambiti ammessi, durata massima |
| POST | `/access-governance/assessments` | Valutazione sincrona | Stesso `source_ref + request_version` → restituisce la valutazione già fatta |
| GET | `/access-governance/assessments/{id}` | Rilettura della valutazione | Include lo stato dell'eventuale approvazione |
| POST | `/access-governance/assessments/{id}/review` | Invio al privacy manager | Crea un'`Approval` (vedi sezione dedicata) |
| POST | `/authorizations` | Registrazione di un accesso concesso | `Idempotency-Key: HELPDESK:<ticket>:<versione>` |
| GET | `/authorizations?user_id=&active_only=true&include_future=true` | Accessi della persona | Con `include_future` anche quelli non ancora iniziati; ogni voce ha `asset_name` e `profile_code` |
| PATCH | `/authorizations/{id}` | Registrazione di una modifica | `If-Match` obbligatorio |
| POST | `/authorizations/{id}/revoke` | Registrazione di una revoca | `If-Match` obbligatorio; con `revocation_reference` |

**Esempio di valutazione.** Richiesta:

```json
{
  "request_type": "GRANT",
  "subject_user_id": "b7c1…",
  "asset_id": "5e02…",
  "profile_id": "91aa…",
  "operations": ["READ"],
  "scope": {"type": "ALL_RECORDS"},
  "duration": {"type": "TEMPORARY", "valid_from": "2026-11-02", "valid_to": "2026-11-30"},
  "current_authorization_id": null,
  "source": {"module": "HELPDESK", "ref": "001-2026-0000412", "request_version": 1,
             "url": "https://helpdesk.ente.it/tickets/412"}
}
```

Risposta:

```json
{
  "id": "c3d9…",
  "outcome": "REVIEW_REQUIRED",
  "profile": {"id": "91aa…", "code": "OPERATORE_ANAGRAFE", "version": 4},
  "processing_activities": [{"id": "…", "code": "TR-0012", "name": "Tenuta dell'anagrafe"}],
  "data_categories": ["Dati anagrafici", "Documenti identificativi"],
  "checks": [
    {"code": "PROFILE_APPROVED", "result": "PASS"},
    {"code": "OPERATION_ALLOWED", "result": "PASS"},
    {"code": "SCOPE_ALLOWED", "result": "FAIL", "message": "Ambito ALL_RECORDS non previsto dal profilo"},
    {"code": "TRAINING_VALID", "result": "WARNING", "message": "Corso PRIVACY_BASE scaduto il 2026-03-31"},
    {"code": "PRIVACY_INSTRUCTIONS_VALID", "result": "NOT_EXECUTABLE"}
  ],
  "links": {"profile": "https://…/authorizations/profiles/91aa…"},
  "created_at": "2026-10-27T10:15:00Z"
}
```

`source.url` è facoltativo: l'indirizzo del ticket, solo `http` o `https` (altrimenti 422). PrivacyPA lo mostra come collegamento nella coda approvazioni.

**Stato della revisione.** La risposta di `POST` e `GET /assessments/{id}` contiene il campo `approval`. Vale `null` se la richiesta non è stata inviata al privacy manager, altrimenti:

```json
"approval": {
  "id": "…",
  "state": "PENDING | DECIDED | CANCELLED",
  "decision": "APPROVED | REJECTED | CHANGES_REQUESTED | null",
  "entity_version": 1,
  "notes": "…",
  "approver_id": "…",
  "requested_at": "…",
  "decision_at": "…"
}
```

`entity_version` è la `request_version` su cui è stata chiesta la revisione: l'HelpDesk ignora la decisione se non coincide con la versione corrente della richiesta. Con `decision: null` non c'è ancora una decisione.

**Altre regole:**

- La risposta contiene solo codici, nomi e messaggi brevi, mai dati delle persone interessate.
- Tempo di risposta della valutazione: sotto 2 secondi. Oltre 10 secondi, o con un errore 5xx, l'HelpDesk considera PrivacyPA non raggiungibile.
- Una richiesta di registrazione ripetuta con la stessa `Idempotency-Key` restituisce la stessa risposta, senza duplicati: l'HelpDesk ritenta le registrazioni in coda.

## Approvazione dei casi non standard

Per i casi non standard si usa il modello `privacypa_approvals` già esistente, con `entity_type = "access_assessment"`. Il privacy manager decide dalla coda approvazioni che usa oggi.

- **Creazione:** `POST /assessments/{id}/review` crea l'`Approval` con `entity_id` = id della valutazione, `entity_version` = versione della richiesta e `request_notes` = motivazione inviata dall'operatore. `requested_by` è il servizio HelpDesk; l'operatore che ha inviato va salvato in un nuovo campo `requested_on_behalf_of`.
- **Scheda nella coda:** la scheda mostra la richiesta, i controlli non superati e il collegamento al ticket. Non mostra la descrizione libera del ticket.
- **Decisioni:**
  - `APPROVED`: l'HelpDesk passa all'approvazione ICT con gli estremi;
  - `REJECTED`: la richiesta è respinta e il ticket si chiude;
  - `CHANGES_REQUESTED`: il ticket torna in "informazioni mancanti".
- **Note obbligatorie** per `REJECTED` e `CHANGES_REQUESTED`: l'HelpDesk le mostra all'operatore.
- **Cambio sostanziale:** se l'HelpDesk invia una nuova versione della richiesta, l'`Approval` aperta sulla versione precedente viene annullata (`cancelled_at`). Le decisioni su una versione superata non sono applicabili, come già avviene con `entity_version`.
- **Separazione dei compiti:** il privacy manager non può decidere su una richiesta che riguarda sé stesso o che ha inviato lui.

## Webhook e accessi scaduti

I nuovi eventi usano lo stesso meccanismo dei webhook esistenti: outbox, firma HMAC, `X-PrivacyPA-Delivery` come chiave di idempotenza e contenuto minimo. L'HelpDesk si registra come endpoint dalla pagina Webhook.

| Evento | Quando | `data` |
| --- | --- | --- |
| `access_approval.decided` | Il privacy manager decide su un caso non standard | `entity_id` (valutazione), `source_ref`, `request_version`, `decision` |
| `authorization_profile.updated` | Un profilo cambia (`row_version` aumenta) o viene disattivato | `entity_id` (profilo), `version`, `active` |
| `authorization.expired_not_revoked` | Un'`Authorization` di origine HelpDesk ha superato `valid_to` senza revoca registrata | `entity_id`, `source_ref`, `valid_to` |

Le note della decisione non viaggiano nel webhook: l'HelpDesk le rilegge con `GET /assessments/{id}`.

**Regola di conformità `AUTHORIZATION_EXPIRED_NOT_REVOKED`** (nuova, nel motore delle regole esistente):

- **Condizione:** `valid_to` superata da più di N giorni (default 3, configurabile), `revoked_at` vuoto e `source_module = HELPDESK`.
- **Effetto:** apre una verifica di conformità (severità `MEDIUM`) e genera il webhook.
- **Contesto:** l'HelpDesk apre in automatico un ticket di revoca alla scadenza degli accessi temporanei. La regola intercetta i casi in cui quel ticket non è stato chiuso.
- **Chiusura:** la verifica si risolve da sola quando la revoca viene registrata.

Come per le altre regole di scadenza, va eseguita con la valutazione periodica già prevista (`POST /api/privacypa/findings/evaluate`).

## Autenticazione e permessi

L'HelpDesk chiama PrivacyPA con un token di servizio del Portal, mai con il token dell'utente che ha aperto il ticket.

- **Token:** l'HelpDesk lo ottiene da `POST /api/service-auth/token` del Portal (`app_id = pramaia-helpdesk` + segreto), come già avviene per le licenze.
- **Riconoscimento in PrivacyPA:** il token del servizio va associato a un ruolo tecnico dedicato, per esempio `INTEGRATION_HELPDESK`, nel catalogo ruoli esistente. Il ruolo ha validità su tutto il tenant e non può essere assegnato a persone.
- **Tracciabilità:** ogni chiamata porta l'header `X-On-Behalf-Of` con l'id dell'operatore HelpDesk. PrivacyPA lo registra nell'audit insieme all'identità del servizio.

**Permessi** (nello stile dei codici esistenti):

| Codice | Chi | Uso |
| --- | --- | --- |
| `access_governance.read` | `INTEGRATION_HELPDESK`, privacy manager | Catalogo di sistemi e profili, lettura delle valutazioni |
| `access_assessment.create` | `INTEGRATION_HELPDESK` | Creare valutazioni e inviarle in revisione |
| `access_assessment.review` | Privacy manager | Decidere sui casi non standard |
| `authorization.create` / `authorization.edit` | `INTEGRATION_HELPDESK` (esistenti) | Registrare concessioni, modifiche e revoche |
| `authorization_profile.manage` | Privacy manager | Gestire i profili estesi |

Il ruolo `INTEGRATION_HELPDESK` non ha `access_assessment.review`: l'HelpDesk non può approvare i propri casi non standard.

**Licenza:** le API `access-governance` rientrano nel modulo `authorization` già presente nel catalogo PrivacyPA. Se il modulo non è concesso rispondono 403 `MODULE_NOT_LICENSED`, e l'HelpDesk disattiva la funzione invece di trattare PrivacyPA come offline.

## Criteri di accettazione e test

Il lavoro è completo quando questi casi passano come test automatici sul backend di PrivacyPA.

| # | Caso | Atteso |
| --- | --- | --- |
| 1 | `GRANT` con profilo attivo, operazioni e ambito ammessi, formazione valida | `STANDARD`, tutti i controlli `PASS` tranne istruzioni `NOT_EXECUTABLE` |
| 2 | Come 1, ma ambito `ALL_RECORDS` non ammesso | `REVIEW_REQUIRED`, `SCOPE_ALLOWED = FAIL` |
| 3 | Operazione `EXPORT` non ammessa | `REVIEW_REQUIRED`, `OPERATION_ALLOWED = FAIL` |
| 4 | Formazione scaduta, policy di default | `STANDARD` con `TRAINING_VALID = WARNING` |
| 5 | Formazione scaduta, policy del tenant bloccante | `REVIEW_REQUIRED` |
| 6 | Sistema non registrato in PrivacyPA | `UNKNOWN` |
| 7 | Temporaneo di 60 giorni su profilo con `max_duration_days = 30` | `REVIEW_REQUIRED`, `TEMPORARY_ACCESS_POLICY = FAIL` |
| 8 | `CHANGE` che riduce le operazioni | `STANDARD` |
| 9 | `REVOKE` di un'`Authorization` attiva | `STANDARD` |
| 10 | Stessa valutazione inviata due volte (stesso `source_ref + request_version`) | Una sola valutazione salvata, stessa risposta |
| 11 | Nuova versione della richiesta con revisione aperta | Revisione precedente annullata |
| 12 | Privacy manager approva | Webhook `access_approval.decided` firmato, con `decision = APPROVED` |
| 13 | `POST /authorizations` ripetuto con la stessa `Idempotency-Key` | Un solo record, stessa risposta |
| 14 | Concessione registrata con estremi | Estremi, ticket di origine e versione del profilo visibili nel registro |
| 15 | Accesso temporaneo scaduto da 4 giorni senza revoca | Verifica `AUTHORIZATION_EXPIRED_NOT_REVOKED` aperta e webhook inviato |
| 16 | Revoca registrata dopo la verifica | Verifica risolta in automatico |
| 17 | Token di servizio che chiama `access_assessment.review` | 403 |
| 18 | Modulo `authorization` non concesso | 403 `MODULE_NOT_LICENSED` su tutte le API `access-governance` |
| 19 | Valutazione su un tenant diverso da quello del token | 403, nessun dato restituito |
| 20 | Asset con responsabile della banca dati compilato | Nome, ruolo ed eventuale id utente restituiti da GET /access-governance/assets |

**Fuori da questa fase:** presa visione delle istruzioni (`PRIVACY_INSTRUCTIONS_VALID`), esiti `HIGH_RISK` e `NOT_ALLOWED`, campagne di revisione periodica.
