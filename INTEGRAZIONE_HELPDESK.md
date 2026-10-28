# Integrazione HelpDesk → PrivacyPA: richieste di accesso

Per chi sviluppa il lato HelpDesk (`services/privacypa_client.py`, `services/access_request_service.py`) e per chi configura un'installazione. Specifica di riferimento: *PrivacyPA – Specifiche per l'integrazione con HelpDesk (richieste di accesso)*. Le richieste ancora aperte verso Portal e HelpDesk sono in *Richieste a Portal e HelpDesk – richieste di accesso*.

**Stato:** implementato e coperto da test automatici in PrivacyPA (`backend/tests/test_privacypa_access_governance.py`, criteri 1-20 della specifica). Allineato alle *Risposte di Portal e HelpDesk* e alle *Richieste dell'HelpDesk ad altri moduli* (A1-A5, R1). Non ancora provato con l'HelpDesk reale: l'HelpDesk deve ancora chiedere il token con tenant e destinatario (H1), e il collegamento nel Portal deve esistere (P2).

## 1. Configurazione di un'installazione

1. **Collegamento tra moduli.** Il Portal emette il token per PrivacyPA solo se esiste un collegamento attivo `pramaia-helpdesk → PramaIA-PrivacyManager`. PrivacyPA lo dichiara in `incoming_links_manifest` alla registrazione (`register_to_portal.py`: scopo `access_requests`, path `/api/privacypa`) e il Portal lo crea quando entrambe le app sono registrate. Per un'installazione già registrata basta rieseguire `register_to_portal.py` dopo l'aggiornamento: trovando l'app già registrata, lo script invia solo i collegamenti dichiarati e il Portal crea quelli mancanti (senza toccare il resto della configurazione). In alternativa si crea a mano in *Collegamenti tra moduli* del Portal. Il Portal non controlla il path: ne basta uno.
2. **Token di servizio.** L'HelpDesk lo chiede al Portal con `POST /api/service-auth/token`, indicando `app_id = pramaia-helpdesk`, il segreto, `tenant_id` del ticket e `audience = PramaIA-PrivacyManager`. Il token dura 15 minuti e contiene `sub = app:pramaia-helpdesk`, `token_type = service`, `aud = PramaIA-PrivacyManager` e `tenant_id`. PrivacyPA rifiuta (401) un token con `aud` destinato a un'altra app e risponde 400 se manca `tenant_id`. I token senza `aud` (utenti) restano validi.
3. **Ruolo.** In PrivacyPA, pagina *Organizzazione → Ruoli privacy* oppure `POST /api/privacypa/roles/assign`, assegnare a `app:pramaia-helpdesk` il ruolo **Integrazione HelpDesk** (`INTEGRATION_HELPDESK`) con ambito intero ente. Il ruolo si assegna solo a utenti `app:…` e solo sull'intero ente.
4. **Webhook.** Pagina *Webhook*: registrare l'endpoint dell'HelpDesk con gli eventi `access_approval.decided`, `authorization_profile.updated` e `authorization.expired_not_revoked`, poi copiare il segreto nelle impostazioni dell'HelpDesk. Ogni ente ha un endpoint e un segreto propri. Se l'HelpDesk è in rete locale o in http, PrivacyPA va configurato con `WEBHOOK_ALLOWED_HOSTS=<host>` (o `WEBHOOK_ALLOW_PRIVATE_NETWORKS=true`) e, per http, `WEBHOOK_ALLOW_HTTP=true`.
5. **Licenza.** Le API `access-governance` richiedono il modulo `access_governance`, oppure `*`. Con una licenza valida che non lo comprende rispondono 403 `MODULE_NOT_LICENSED`, anche se la lista dei moduli è vuota (come nell'HelpDesk, lista vuota = nessun modulo). Il resto di PrivacyPA non richiede codici di licenza, quindi una licenza emessa senza moduli non blocca le funzioni esistenti. Senza una licenza verificabile le API non si bloccano, come previsto dallo standard dei moduli di licenza. Il catalogo è pubblicato su `GET /api/license/modules` e arriva al LicServer con il file di richiesta di licenza del Portal: nel LicServer non c'è niente da configurare.

   **Quali moduli chiedere.** La funzione si accende solo se l'ente ha **entrambi** i moduli: `access_governance` nella licenza di PrivacyPA e `access_requests` in quella dell'HelpDesk. Con uno solo dei due resta spenta. Sono due licenze distinte e chi le emette nel LicServer vede una licenza alla volta, quindi deve chiederli l'ente: nella richiesta di licenza del Portal l'amministratore indica `access_governance` tra i moduli richiesti per PrivacyPA e `access_requests` per l'HelpDesk, e il LicServer li trova già selezionati all'emissione. Il file di richiesta va esportato dal Portal aggiornato (dal 27 ottobre 2026): quelli esportati prima vengono rifiutati dal LicServer e vanno riesportati. La descrizione di entrambi i moduli nel catalogo ricorda che servono insieme.
6. **Anagrafica.** Per verificare la persona PrivacyPA legge `GET /api/tenants/{tenant_id}/users` dal Portal con il proprio token di servizio. Servono `PORTAL_URL`, `APP_ID` e `APP_CLIENT_SECRET`, e PrivacyPA deve essere abilitato nel tenant.
7. **Profili e sistemi.** Il privacy manager completa i profili in *Profili di autorizzazione*: sistema, operazioni e ambiti ammessi, categorie di dati, trattamenti, corsi richiesti, durata massima. Completa anche il responsabile della banca dati nella scheda del sistema.
8. **Formazione bloccante (facoltativo).** `PUT /api/privacypa/policy/integrations` con `{"access_training_blocking": true}`: la formazione mancante o scaduta rende la richiesta `REVIEW_REQUIRED` invece di dare un avviso.
9. **Valutazione giornaliera delle regole.** PrivacyPA valuta le regole di ogni ente ogni giorno alle `FINDINGS_EVALUATION_HOUR` UTC (predefinita 2). Con più istanze dell'applicazione impostare `FINDINGS_SCHEDULER=false` su tutte tranne una.

Ogni chiamata dell'HelpDesk porta `X-On-Behalf-Of` con l'id dell'operatore: l'audit lo registra accanto al servizio. Per le chiamate degli utenti l'header è ignorato.

**Tenant.** Vale la convenzione della piattaforma: un token, un tenant. Il tenant della richiesta è il `tenant_id` del token, e un `X-Tenant-ID` diverso riceve 403, per i token di servizio come per quelli degli utenti. Nell'interfaccia di PrivacyPA il selettore dell'header cambia tenant chiedendo un nuovo token al Portal (proxy `GET /api/tenants/me` e `POST /api/tenants/switch`, presi dall'AppTemplate).

## 2. Catalogo

`GET /api/privacypa/access-governance/assets` restituisce i sistemi con almeno un profilo attivo:

```json
[{"id": "…", "code": "AS-0003", "name": "Anagrafe", "asset_type": "APPLICATION",
  "data_owner": {"name": "Mario Bianchi", "role": "Dirigente Servizi Demografici", "user_id": "…"}}]
```

`GET /api/privacypa/access-governance/profiles?asset_id=…` restituisce i profili attivi del sistema: `id`, `code`, `name`, `description`, `version`, `allowed_operations`, `allowed_scope_types`, `max_duration_days`, `processing_activities` (`id`, `code`, `name`), `data_categories` (nomi) e `required_courses` (`id`, `code`, `title`).

## 3. Valutazione

`POST /api/privacypa/access-governance/assessments` accetta il corpo della specifica più `source.url`, facoltativo: l'indirizzo del ticket (solo `http` o `https`, massimo 500 caratteri, altrimenti 422), mostrato come collegamento nella coda *Approvazioni*. Risponde 201 alla prima valutazione e 200 con la stessa risposta se `source.ref` e `source.request_version` erano già stati valutati. Per `GRANT` e `CHANGE` operazioni e ambito sono obbligatori; un accesso `TEMPORARY` richiede `valid_to`. Una richiesta non valutabile riceve 400, non un esito.

Esiti: `STANDARD`, `REVIEW_REQUIRED`, `UNKNOWN`. Controlli:

| Codice | Quando | Esito se non superato |
| --- | --- | --- |
| `PROFILE_APPROVED` | profilo esistente, attivo, del sistema richiesto | `FAIL` |
| `OPERATION_ALLOWED` | operazioni comprese nel profilo | `FAIL` |
| `SCOPE_ALLOWED` | tipo di ambito ammesso | `FAIL` |
| `TEMPORARY_ACCESS_POLICY` | con durata massima: accesso temporaneo entro il limite (giorni contati inclusi gli estremi) | `FAIL` |
| `TRAINING_VALID` | corsi richiesti completati e non scaduti (un esonero vale come completamento) | `WARNING`, o `FAIL` con la policy dell'ente |
| `ALREADY_AUTHORIZED` | solo `GRANT`: autorizzazione con lo stesso profilo in vigore o con inizio futuro | `WARNING` |
| `PRIVACY_INSTRUCTIONS_VALID` | sempre `NOT_EXECUTABLE` (Release 2) | — |
| `SUBJECT_RESOLVED` | la persona è un utente del Portal attivo e membro attivo dell'ente (`is_active` e `membership_active`); per `REVOKE` basta l'id (`NOT_APPLICABLE`) | `FAIL` ed esito `UNKNOWN`; `NOT_EXECUTABLE` se il Portal non risponde, senza cambiare l'esito |
| `SYSTEM_REGISTERED` | sistema non registrato nell'ente | esito `UNKNOWN` |
| `CURRENT_AUTHORIZATION` | `CHANGE`/`REVOKE`: autorizzazione indicata, della stessa persona, non revocata; per `CHANGE` anche non scaduta (un accesso non ancora iniziato si può modificare, uno scaduto va richiesto di nuovo con `GRANT`) | esito `UNKNOWN` |
| `CHANGE_REDUCES_PRIVILEGES` | `CHANGE` che non allarga nulla | `PASS` |

Gli ultimi cinque codici non sono nella specifica: spiegano gli esiti `UNKNOWN` e il caso di riduzione dei privilegi. L'anagrafica è letta con una cache di 5 minuti per tenant e riletta subito per un id sconosciuto.

**Modifica che riduce i privilegi** (esito `STANDARD` senza altri controlli): stesso profilo, operazioni comprese in quelle attuali, ambito uguale (o attuale `ALL_RECORDS`), scadenza non posticipata. Le autorizzazioni registrate prima dell'integrazione, senza operazioni né ambito, vanno sempre ai controlli completi.

La risposta contiene solo codici, nomi e messaggi brevi, mai la persona:

```json
{"id": "…", "outcome": "REVIEW_REQUIRED", "request_type": "GRANT",
 "source": {"module": "HELPDESK", "ref": "001-2026-0000412", "request_version": 1, "url": "https://helpdesk.ente.it/tickets/412"},
 "profile": {"id": "…", "code": "OPERATORE_ANAGRAFE", "name": "Operatore anagrafe", "version": 4},
 "asset": {"id": "…", "code": "AS-0003", "name": "Anagrafe"},
 "processing_activities": [{"id": "…", "code": "TR-0012", "name": "Tenuta dell'anagrafe"}],
 "data_categories": ["Dati anagrafici"], "request": {"operations": ["READ"], "scope": {…}, "duration": {…}},
 "checks": [{"code": "SCOPE_ALLOWED", "result": "FAIL", "message": "Ambito ALL_RECORDS non previsto dal profilo"}, …],
 "approval": null, "links": {"profile": "/profili-autorizzazione?id=…"}, "created_at": "2026-10-27T10:15:00Z"}
```

`GET /api/privacypa/access-governance/assessments/{id}` rilegge la valutazione. Il campo `approval` vale `null` oppure:

```json
{"id": "…", "state": "PENDING | DECIDED | CANCELLED", "decision": "APPROVED | REJECTED | CHANGES_REQUESTED | null",
 "entity_version": 1, "notes": "…", "approver_id": "…", "requested_at": "…", "decision_at": "…"}
```

`entity_version` è la `request_version` della richiesta.

## 4. Casi non standard

`POST /api/privacypa/access-governance/assessments/{id}/review` con `{"notes": "motivazione"}` mette la richiesta nella coda *Approvazioni* del privacy manager (permesso `access.approve`). Risposte:

- **409** se la valutazione è `STANDARD`, se esiste una versione più recente della richiesta o se la richiesta è già stata decisa;
- un invio ripetuto restituisce la revisione già in coda.

Regole:

- **Nuova versione:** una valutazione di una nuova versione annulla la revisione aperta sulle versioni precedenti (`state = CANCELLED`).
- **Segregazione:** non decide chi riceve l'accesso né l'operatore che l'ha inviato (`X-On-Behalf-Of`). Questo vale sempre, indipendentemente dalla policy dell'ente.
- **Note:** obbligatorie per `REJECTED` e `CHANGES_REQUESTED`. Non viaggiano nel webhook e si rileggono con il `GET`.

La scheda nella coda mostra: tipo di richiesta, numero del ticket (collegamento al ticket se è arrivato `source.url`), persona, operatore, sistema, profilo, operazioni, ambito, durata e controlli non superati.

## 5. Registro degli autorizzati

`POST /api/privacypa/authorizations` accetta i campi esistenti più:

- **estremi:** `authorized_by_name`, `authorization_reference`, `authorization_date`, `authorization_notes`;
- **contenuto:** `operations`, `scope` (`{type, value}`), `profile_version`;
- **origine:** `source_module`, `source_ref`, `forced`.

Con `source_module` impostato sono obbligatori `source_ref` e gli estremi. Se `profile_version` manca vale la versione corrente del profilo.

**Nessun duplicato.** Oltre a `Idempotency-Key` (24 ore), una concessione con stessi `source_module`, `source_ref`, persona e profilo già registrata viene restituita con 200 invece di crearne un'altra.

`PATCH /api/privacypa/authorizations/{id}` (con `If-Match`) è una modifica parziale: i campi non inviati restano invariati e `user_id` si può omettere (non si cambia).

`POST /api/privacypa/authorizations/{id}/revoke` (con `If-Match`) accetta `reason` o `revoked_reason`, più `revocation_reference`.

`GET /api/privacypa/authorizations` e `GET /api/privacypa/authorizations/{id}` restituiscono anche `asset_name` e `profile_code`, valorizzati anche per sistemi e profili disattivati. Filtri dell'elenco: `user_id`, `asset_id`, `processing_id`; `active_only=true` restituisce le autorizzazioni in vigore oggi, e con `include_future=true` anche quelle non ancora iniziate (non revocate e non scadute), per poterle modificare o revocare prima dell'inizio.

L'account di servizio legge il registro (`GET`, serve per l'`ETag`) ma non gestisce i profili: creazione, modifica ed eliminazione richiedono `authorization.administer`.

## 6. Webhook

Formato, firma e ritentativi sono quelli di `WEBHOOKS.md`. Gli eventi sono:

| Evento | Quando | `data` |
| --- | --- | --- |
| `access_approval.decided` | decisione del privacy manager su una richiesta di accesso | `entity_type = access_assessment`, `entity_id` (valutazione), `source_ref`, `request_version`, `decision`, `approval_id`, `entity_version` |
| `authorization_profile.updated` | profilo modificato o eliminato | `entity_type = authorization_profile`, `entity_id`, `version`, `active` |
| `authorization.expired_not_revoked` | verifica aperta su un accesso registrato dall'HelpDesk scaduto e non revocato | `entity_type = authorization`, `entity_id`, `source_ref`, `valid_to`, `rule_code`, `rule_version` |

**Regola sugli accessi scaduti.** È la regola già esistente `AUTHORIZATION_EXPIRED_NOT_REVOKED`, estesa:

- per gli accessi con `source_module = HELPDESK` si apre dopo `helpdesk_grace_days` giorni dalla scadenza (default 3, modificabile come gli altri parametri delle regole), con gravità media, e genera il webhook;
- per gli altri accessi resta com'era: dal giorno dopo la scadenza, gravità alta, senza webhook.

La verifica si chiude da sola alla valutazione successiva alla revoca. Le regole si valutano ogni giorno (§1, punto 9), all'apertura della pagina delle verifiche e con `POST /api/privacypa/findings/evaluate`.

## 7. Permessi

| Codice | Ruoli di default | Note |
| --- | --- | --- |
| `access.view` | admin, privacy manager, DPO, `INTEGRATION_HELPDESK` | catalogo e valutazioni |
| `access.create` | admin, `INTEGRATION_HELPDESK` | valutazioni e invio in revisione; non modificabile per ente |
| `access.approve` | admin, privacy manager | decisione sui casi non standard |
| `authorization.view/create/edit` | come prima + `INTEGRATION_HELPDESK` | registro |
| `authorization.administer` | admin, privacy manager | profili di accesso (prima bastava `authorization.create/edit`) |

Gli account di servizio (`INTEGRATION`, `INTEGRATION_HELPDESK`) non compaiono tra i ruoli di "tutti" e non si aggiungono ad altri permessi con gli override dell'ente: l'HelpDesk non vede gli altri moduli e non può decidere.

## 8. Differenze rispetto alla specifica

- **Codici dei permessi:** seguono la convenzione `modulo.azione` (`access.*`, `authorization.administer`) invece di `access_governance.read`, `access_assessment.*` e `authorization_profile.manage`.
- **Regola sugli accessi scaduti:** si estende quella esistente invece di crearne una nuova (vedi §6).
- **Controlli aggiuntivi:** cinque codici in più (vedi §3).
- **`X-Tenant-ID`:** solo un controllo di coerenza col token, secondo la convenzione del Portal (P1). La specifica lo indicava come selettore del tenant.
- **Persona non risolvibile:** id mancante, persona assente dall'anagrafica del Portal o disattivata. Con il Portal non raggiungibile la verifica non si fa e non blocca.
- **Collegamento al ticket:** la specifica non lo prevedeva; l'HelpDesk invia `source.url` (A3).
- **Accessi non ancora iniziati:** `include_future` nel registro e `CHANGE` ammesso prima dell'inizio (A2).
