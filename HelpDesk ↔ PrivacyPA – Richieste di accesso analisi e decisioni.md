# HelpDesk ↔ PrivacyPA – Richieste di accesso: analisi e decisioni

Oct 27, 2026 · @Someone

## Sintesi

La proposta v1.0 (*HelpDesk ↔ PrivacyPA – Specifica funzionale e tecnica per la gestione privacy-aware delle richieste di accesso*) è valida nell'impostazione e si adotta con le modifiche descritte qui. Il documento confronta la proposta con il codice attuale di HelpDesk e di PrivacyPA (cartella `PramaIA-PrivacyManager`) e fissa le decisioni prese.

Decisioni principali:

- **Chi approva:** un operatore ICT abilitato per la categoria "richiesta di accesso". Approva sulla base dell'autorizzazione del responsabile della banca dati, i cui estremi sono obbligatori.
- **Ruolo di PrivacyPA:** fornisce il contesto (profilo, trattamenti, formazione) e una valutazione. Non decide e non controlla l'ufficio di appartenenza.
- **Casi non standard** (`REVIEW_REQUIRED`, `HIGH_RISK`): l'approvazione passa al privacy manager in PrivacyPA.
- **PrivacyPA non raggiungibile:** avviso nel ticket e blocco delle sole azioni privacy. Solo gli Admin ICT possono forzare l'approvazione, con motivazione e controllo a posteriori.
- **Riuso:** si estende quello che esiste già (registro `Authorization` di PrivacyPA, riferimenti tra moduli, webhook, permessi per categoria). Nessuna tabella parallela e nessun event bus.
- **Fuori dall'MVP:** verifica automatica del provisioning, connettori IAM, campagne di revisione.

## Valutazione della proposta v1.0

Si conservano i principi; cambiano il modello di approvazione, il modello dati e il perimetro dell'MVP.

**Si conserva:**

- separazione dei compiti: HelpDesk gestisce il flusso, PrivacyPA valuta, ICT/IAM esegue (§2, §48);
- l'AI classifica e suggerisce, ma non approva, non nega e non assegna profili (§33);
- il DPO è coinvolto solo quando serve (§25);
- un cambio sostanziale invalida valutazione e approvazione (§43);
- minimizzazione dei dati nel ticket e notifiche senza dettagli sensibili (§52, §56);
- requisiti di sicurezza: isolamento tra tenant, audit, autenticazione tra servizi (§55).

**Si cambia:**

| Punto della proposta | Decisione | Motivo |
| --- | --- | --- |
| §24 Approvatori configurabili (line manager, data owner…) | Approva un operatore ICT abilitato per la categoria | L'ICT esegue sulla base dell'autorizzazione del responsabile della banca dati |
| §16 Nuova tabella `privacy_access_profiles` | Estendere `AuthorizationProfile` di PrivacyPA | Profili e registro autorizzati esistono già |
| §18 Controllo `ORGANIZATIONAL_UNIT_ALLOWED` | Eliminato | I profili non corrispondono sempre agli uffici; decide l'operatore |
| §35 Undici nuovi stati del ticket | Stati sulla richiesta di accesso, ticket invariato | Gli stati ticket sono configurabili e con nome unico globale |
| §30 URI `privacy://…` | Tabella `helpdesk_ticket_entity_refs` esistente | Fa già la stessa cosa |
| §40 Event bus | API + webhook firmati di PrivacyPA | Nessun broker nell'ecosistema; installazione on-premise |
| §28 Verifica requested = approved = provisioned | Release 2 | Con provisioning manuale l'esito sarebbe quasi sempre `NOT_VERIFIABLE` |
| §45 Permessi `helpdesk.access_request.*` | Flag sul permesso di categoria + ruolo admin | L'HelpDesk non usa permessi granulari |
| §4 `privacy_relevant` + `privacy_relevance_status` | Un solo campo, derivato dalla categoria | I due campi sono ridondanti |

## Cosa esiste già e si riusa

Buona parte di ciò che la proposta descrive come nuovo esiste già in uno dei due moduli.

| Modulo | Elemento esistente | Uso nell'integrazione |
| --- | --- | --- |
| PrivacyPA | `privacypa_authorization_profiles` (oggi solo nome e descrizione) | Diventa il profilo di accesso: si aggiungono sistema, categorie di dati, operazioni, ambito, trattamenti, istruzioni e formazione richieste |
| PrivacyPA | `privacypa_authorizations` (utente, trattamento/asset, profilo, validità, revoca, ultima revisione) | Registro degli autorizzati: a fine provisioning l'HelpDesk crea o revoca il record |
| PrivacyPA | Formazione (`training_courses`, `assignments`, `completions`) | Controllo `TRAINING_VALID` |
| PrivacyPA | `privacypa_approvals`, `privacypa_tenant_policies` | Approvazione dei casi non standard da parte del privacy manager |
| PrivacyPA | Webhook firmati HMAC con outbox, `POST /integration/events` con idempotenza | Notifiche PrivacyPA → HelpDesk (esito approvazione, profilo cambiato) |
| PrivacyPA | Permessi a stringa (`require_permission(...)`) | I permessi `privacy.access_*` del §45 si aggiungono senza cambiare modello |
| HelpDesk | `helpdesk_ticket_entity_refs` | Collegamenti dal ticket a profilo, trattamenti e autorizzazione in PrivacyPA |
| HelpDesk | `helpdesk_operator_category_permissions` | Base per l'abilitazione ad approvare per categoria |
| HelpDesk | `helpdesk_activity_log` | Traccia degli eventi del ticket (va rafforzata, vedi sezione HelpDesk) |
| HelpDesk | Notifiche push via Prama-Comm (`TicketEventNotificationRule`) | Avvisi a operatori e richiedente |
| HelpDesk | Catalogo moduli di licenza (`MODULE_CATALOG`) | Nuovo modulo dedicato alle richieste di accesso |
| HelpDesk | Token di servizio verso il Portal (`portal_license_client`) | Modello per l'autenticazione HelpDesk → PrivacyPA |

## Modello di approvazione

Approva un operatore ICT abilitato per la categoria "richiesta di accesso". Non decide nel merito: registra ed esegue l'autorizzazione del responsabile della banca dati.

- **Abilitazione:** un flag "può approvare" sul permesso dell'operatore per la categoria, impostato dalla stessa schermata dei permessi. È separato dai livelli di visibilità (0/1/2) e non è un livello in più.
- **Nessun approvatore esterno al team ICT.** Non servono line manager, organigramma o nuove regole di visibilità: chi approva vede già i ticket della categoria.
- **Nessun controllo sull'ufficio.** I profili non corrispondono sempre agli uffici e l'operatore sa se la richiesta è legittima. Nel modulo l'ufficio è un campo facoltativo e solo informativo.
- **Valutazione di PrivacyPA come contesto:** profilo esistente e attivo, operazioni comprese nel profilo, formazione della persona valida (presa visione delle istruzioni in Release 2), scadenza per gli accessi temporanei, trattamenti collegati.
- **Casi non standard.** Con `REVIEW_REQUIRED` o `HIGH_RISK` l'approvazione avviene in PrivacyPA, a cura del privacy manager. L'HelpDesk resta in attesa dell'esito via webhook, poi l'operatore ICT esegue.
- **Separazione dei compiti:**
  - chi ha chiesto l'accesso non può approvarlo: regola sempre attiva, anche per gli admin;
  - chi approva non esegue: regola configurabile per tenant.

## Estremi di autorizzazione

Senza gli estremi dell'autorizzazione rilasciata dal responsabile della banca dati non si può approvare. Sono campi distinti, non una nota libera, perché devono valere come evidenza e comparire nei report.

| Campo | Obbligatorio | Esempio |
| --- | --- | --- |
| Autorizzato da (nome e ruolo del responsabile della banca dati) | Sì | Dirigente Servizi Demografici |
| Riferimento del documento (protocollo, nota, email) | Sì | Prot. n. 12345 |
| Data dell'autorizzazione | Sì | 20/10/2026 |
| Note | No | — |
| Allegato (copia dell'autorizzazione) | No | PDF della nota |

Regole:

- **L'allegato è ammesso**, perché l'autorizzazione non contiene i dati a cui si chiede l'accesso.
- **Valgono anche per l'approvazione forzata.** La forzatura salta il controllo di PrivacyPA, non l'autorizzazione del responsabile.
- **Un cambio sostanziale li invalida.** Se dopo l'approvazione cambiano profilo, operazioni, ambito o durata, servono nuovi estremi oltre alla nuova valutazione.
- **Vanno anche in PrivacyPA.** Il record `Authorization` va esteso con questi campi, così il registro degli autorizzati mostra su quale base è stato concesso ogni accesso. Oggi ha solo `granted_by`, un id utente.
- **Il responsabile viene proposto in automatico.** PrivacyPA aggiunge agli asset il responsabile della banca dati (nome e ruolo). Il modulo dell'HelpDesk lo precompila nel campo "Autorizzato da"; l'operatore può correggerlo.

## Flusso e stati della richiesta di accesso

Tutte le strade portano all'approvazione ICT con gli estremi: in via ordinaria (`STANDARD`), dopo il privacy manager (casi non standard) o per forzatura (PrivacyPA offline).

&#91;embedded content: flusso della richiesta di accesso · 7 passi, 3 diramazioni\]

Anche l'operatore ICT può respingere la richiesta; il ticket si chiude come respinto.

**Stati della richiesta di accesso:**

| Campo | Valori |
| --- | --- |
| `assessment_status` | `PENDING`, `STANDARD`, `REVIEW_REQUIRED`, `UNKNOWN`, `UNAVAILABLE`, `INVALIDATED` |
| `approval_status` | `PENDING`, `PRIVACY_REVIEW`, `APPROVED`, `FORCED`, `REJECTED` |
| `provisioning_status` | `NOT_STARTED`, `DONE` |
| `sync_status` | `NOT_REQUIRED`, `PENDING`, `SENT` |

**Regole:**

- Il ticket passa a uno stato finale solo con richiesta respinta o provisioning eseguito. La registrazione in PrivacyPA può essere ancora in coda.
- Un cambio sostanziale (persona, sistema, profilo, operazioni, ambito, durata) riporta valutazione e approvazione a `INVALIDATED` / `PENDING` e richiede nuovi estremi.
- Gli stati del ticket restano quelli configurati dal tenant: nessuno stato nuovo globale.

## PrivacyPA non raggiungibile e approvazione forzata

Se PrivacyPA non risponde, il ticket mostra un avviso e si bloccano solo le azioni privacy. Il resto del lavoro continua.

**Comportamento durante il fermo:**

- **Bloccate:** approvazione, abilitazione e chiusura. Commenti e richieste di informazioni restano possibili.
- **Nessun blocco se la valutazione c'è già.** Se una valutazione valida è salvata e non ci sono state modifiche sostanziali, si può approvare sulla base di quella.
- **La registrazione in PrivacyPA va in coda.** Il record `Authorization` viene inviato quando PrivacyPA torna disponibile; nel frattempo il ticket mostra "registrazione in attesa".
- **Nuovi tentativi automatici.** Un processo in background ripete valutazioni e registrazioni sospese, sul modello dello scheduler email. Il ticket si sblocca da solo.
- **SLA sospeso durante il fermo.** Il servizio SLA oggi non prevede sospensioni: va aggiunto.
- **Distinguere il fermo dall'assenza.** Se PrivacyPA non è installato o non ha licenza, la funzione è disattivata e non blocca nessun ticket.

**Approvazione forzata:**

- **Chi:** solo gli Admin ICT, cioè `helpdesk_role = admin` nel tenant. Gli amministratori del Portal sono esclusi.
- **Quando:** solo se PrivacyPA non è raggiungibile. Non serve a ribaltare un esito `REVIEW_REQUIRED` o `HIGH_RISK`.
- **Cosa serve:** motivazione obbligatoria più gli estremi di autorizzazione. Un admin non può forzare una richiesta fatta da lui.
- **Traccia:** evento dedicato nel log e indicatore ben visibile sul ticket.
- **Controllo a posteriori.** Quando PrivacyPA torna disponibile, la valutazione parte da sola. Se l'esito non è `STANDARD`, gli Admin ICT ricevono un avviso e in PrivacyPA si apre una verifica.

## Modifiche lato HelpDesk

Il ticket resta com'è; il ciclo di vita dell'accesso vive in una nuova entità collegata al ticket. Le modifiche sono tutte additive (migrazione 022 e seguenti).

| Area | Modifica |
| --- | --- |
| Categorie | Flag `is_access_request` su `helpdesk_categories`: un ticket in questa categoria richiede la richiesta di accesso strutturata |
| Permessi | Flag `can_approve` su `helpdesk_operator_category_permissions` |
| Nuova tabella `helpdesk_access_requests` | Dati della richiesta: ticket, tipo (`GRANT` / `CHANGE` / `REVOKE`), persona, sistema e profilo (id PrivacyPA + etichetta salvata), operazioni, ambito, durata, validità, motivazione, ufficio facoltativo, `version` |
|  | Stato: `assessment_status` + esito salvato, `approval_status`, `provisioning_status`, `sync_status` verso PrivacyPA |
|  | Approvazione: estremi di autorizzazione, chi ha approvato e quando, `forced` + motivazione, id dell'`Authorization` creata in PrivacyPA |
| Chiusura del ticket | `update_ticket` rifiuta il passaggio a uno stato finale se la richiesta di accesso non è conclusa |
| Cancellazione | `DELETE /tickets/{id}` rifiutato per i ticket con richiesta di accesso: oggi il log è in cascata e le evidenze andrebbero perse |
| Activity log | Per gli eventi della richiesta di accesso un errore di scrittura annulla l'operazione; oggi `log_activity` lo ignora |
| Servizi | `PrivacyPAClient` (token di servizio, timeout, stato di raggiungibilità), `AccessRequestService`, job di nuovi tentativi |
| API | Nuovo file `access_requests_api.py` (regola delle 800 righe): richiesta, valutazione, approvazione, approvazione forzata, rifiuto, provisioning |
| Webhook in ingresso | Endpoint che riceve gli eventi firmati di PrivacyPA (esito approvazione, profilo modificato) e ne verifica la firma |
| SLA | Sospensione sempre attiva durante il fermo di PrivacyPA e durante l'approvazione del privacy manager |
| Email-to-ticket | Una richiesta arrivata per email entra in "informazioni mancanti". Per i tenant con il modulo attivo solo modello LLM locale (Ollama o Mind) |
| Licenza | Nuovo modulo `access_requests` nel `MODULE_CATALOG`, enforcement `ui+api` |
| Frontend | Campi strutturati in apertura ticket, pannello privacy nel dettaglio, finestra di approvazione con estremi, forzatura per admin, flag nella pagina permessi, traduzioni in 5 lingue; nell'app mobile pannello privacy e approvazione con estremi |

## Modifiche lato PrivacyPA e contratto

Il lavoro su PrivacyPA lo svolge il team PrivacyPA, sulla base delle specifiche dedicate: PrivacyPA – Specifiche per l'integrazione con HelpDesk (richieste di accesso). In sintesi:

- `AuthorizationProfile` e `Authorization` estesi: sistema, dati, operazioni, ambito e requisiti del profilo; estremi, ticket di origine e versione del profilo nell'autorizzazione.
- Nuovo router `/api/privacypa/access-governance` per sistemi, profili e valutazioni. Il registro si aggiorna tramite l'API `/api/privacypa/authorizations` esistente.
- Controlli MVP: `PROFILE_APPROVED`, `OPERATION_ALLOWED`, `SCOPE_ALLOWED`, `TEMPORARY_ACCESS_POLICY`, `TRAINING_VALID`, `ALREADY_AUTHORIZED`. La presa visione delle istruzioni passa alla Release 2, perché in PrivacyPA manca il modello.
- Casi non standard sulle approvazioni esistenti (`entity_type = access_assessment`).
- Webhook `access_approval.decided`, `authorization_profile.updated`, `authorization.expired_not_revoked` e regola di conformità sugli accessi scaduti e non revocati.
- Token di servizio del Portal associato al ruolo tecnico `INTEGRATION_HELPDESK`, che non può decidere sui casi non standard.

## Ticket a scadenza (tutte le categorie)

Qualsiasi ticket può avere un periodo di validità: alla scadenza l'HelpDesk apre in automatico un nuovo ticket collegato. Per le richieste di accesso temporanee il ticket generato è la revoca.

**In apertura del ticket:** toggle *"Richiede ticket a scadenza?"*. Se attivo, compaiono due date: *Dal* (facoltativa, default oggi) e *Al* (obbligatoria, successiva a *Dal*). Nella categoria "richiesta di accesso" queste date sono anche la durata dell'accesso temporaneo, senza un secondo campo.

**Ticket generato:**

| Campo | Valore |
| --- | --- |
| Quando | All'inizio dell'ultimo giorno di validità (*Al*); anticipo in giorni configurabile per categoria, default 0 |
| Categoria | La stessa del ticket originale, oppure quella impostata nella categoria come "categoria del ticket di scadenza" |
| Titolo | "Scadenza: " + titolo originale |
| Descrizione | Riferimento al ticket originale e periodo di validità; la descrizione originale non viene copiata |
| Richiedente | Lo stesso del ticket originale |
| Assegnatario | L'operatore assegnato all'originale se ancora attivo, altrimenti le regole di assegnazione normali |
| Collegamento | Riferimento reciproco tra i due ticket tramite `helpdesk_ticket_entity_refs` (`module_code = helpdesk`, `entity_type = ticket`) |
| Richiesta di accesso | Tipo `REVOKE`, precompilata con persona, sistema, profilo e `Authorization`. Non servono nuovi estremi: la revoca esegue un'autorizzazione che prevedeva già la scadenza |

**Regole:**

- Per le richieste di accesso il ticket di revoca si genera solo se l'accesso è stato effettivamente abilitato (provisioning eseguito).
- Per le altre categorie, chiudendo il ticket l'operatore può annullare la scadenza programmata, per esempio se l'intervento non è stato fatto.
- Le date si possono modificare finché il ticket di scadenza non è stato generato; ogni modifica va nel log.
- La proroga di un accesso è una richiesta `CHANGE` con nuova scadenza: annulla la scadenza precedente e ne programma una nuova.
- La generazione avviene tramite un job in background, sul modello dello scheduler email. È idempotente (al massimo un ticket generato per ticket originale) e recupera le scadenze perse se il server è rimasto spento.
- Il nuovo ticket segue le normali regole di notifica (`ticket_created`).
- Lista ticket: indicatore "scade il …" e filtro "in scadenza".

**Modello dati:**

- `helpdesk_tickets`: `expiry_from`, `expiry_to`, `expiry_status` (`NONE`, `SCHEDULED`, `GENERATED`, `CANCELLED`), `follow_up_ticket_id`, `origin_ticket_id`.
- `helpdesk_categories`: `expiry_follow_up_category_id`, `expiry_lead_days`.

La funzione va anche nell'app mobile (toggle e date in apertura ticket).

## Fasi di rilascio

L'MVP copre concessione, modifica e revoca con approvazione ICT ed evidenze complete. Il resto della proposta va nelle release successive.

**MVP:**

1. Categoria "richiesta di accesso" e campi strutturati in apertura ticket.
2. Profili estesi in PrivacyPA e valutazione sincrona (`STANDARD` / `REVIEW_REQUIRED` / `UNKNOWN`).
3. Pannello privacy nel ticket e collegamenti a PrivacyPA.
4. Approvazione ICT con estremi di autorizzazione; casi non standard approvati in PrivacyPA.
5. Provisioning manuale e registrazione dell'`Authorization` in PrivacyPA.
6. Gestione del fermo di PrivacyPA, approvazione forzata e controllo a posteriori.
7. Blocchi su chiusura e cancellazione, log obbligatorio, invalidazione per cambio sostanziale.
8. Notifiche via Prama-Comm, senza dettagli sensibili.
9. Modulo di licenza dedicato.
10. Ticket a scadenza per tutte le categorie, con revoca automatica degli accessi temporanei.

**Release 2:** esiti `HIGH_RISK` e `NOT_ALLOWED`, presa visione delle istruzioni, campagne di revisione, verifica del provisioning, connettori IAM/AD, report dedicati.

**Fuori perimetro:** decisioni autonome dell'AI, motore avanzato di separazione dei compiti, policy-as-code.

## Decisioni sulle questioni aperte

| Questione | Decisione |
| --- | --- |
| Revoca | È una richiesta come le altre (tipo `REVOKE`), con gli estremi. Eccezione: gli accessi temporanei, revocati con un ticket generato in automatico alla scadenza, senza nuovi estremi |
| Lavoro su PrivacyPA | Lo svolge il team PrivacyPA sulla base delle specifiche dedicate: PrivacyPA – Specifiche per l'integrazione con HelpDesk (richieste di accesso) |
| SLA | Sempre sospeso durante l'approvazione del privacy manager e durante il fermo di PrivacyPA; da rivedere dopo i primi mesi |
| App mobile | Pannello privacy e approvazione con estremi anche da mobile; l'approvazione forzata resta solo da web |
| Ticket già aperti | Nessuna conversione |
| Ticket a scadenza | Disponibile per tutte le categorie, come funzione di base (non è un modulo di licenza) |
| Responsabile della banca dati | Nuovo campo sugli asset di PrivacyPA; l'HelpDesk lo propone negli estremi di autorizzazione |
