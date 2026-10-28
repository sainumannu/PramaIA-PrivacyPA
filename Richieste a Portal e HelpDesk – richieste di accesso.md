# Richieste a Portal e HelpDesk per l'integrazione delle richieste di accesso

27 ottobre 2026 · team PrivacyPA

## Perché questo documento

Stiamo implementando in PrivacyPA quanto descritto in *PrivacyPA – Specifiche per l'integrazione con HelpDesk (richieste di accesso)*. Abbiamo confrontato le specifiche con il codice di PrivacyPA, con l'integrazione già scritta nell'HelpDesk (`privacypa_client.py`, `access_request_service.py`, `access_requests_api.py`) e con il Portal.

Ne sono uscite alcune richieste e domande per i vostri team. In fondo trovate anche cosa adeguiamo da parte nostra, così sapete cosa non serve cambiare da voi.

## Richieste al team Portal

### P1. PrivacyPA è rimasto fuori dall'aggiornamento multi-tenant

**Situazione.** Le altre app ricevono un token utente con `tenant_id` (tenant attivo) e `tenants` (tutti quelli accessibili). Scelgono il tenant con l'header `X-Tenant-ID`, verificato contro `tenants` (nell'HelpDesk: `get_effective_tenant` in `backend/auth/tenant_utils.py`), e hanno il selettore del tenant nell'header della UI. PrivacyPA legge invece solo il claim `tenant_id`, ignora `X-Tenant-ID` e non ha il selettore.

**Richiesta.**

- Confermate che la convenzione di riferimento è quella dell'HelpDesk: header `X-Tenant-ID` verificato contro `tenants` e, in mancanza dell'header, tenant attivo del token.
- Diteci se l'aggiornamento comprendeva altro da recepire, per esempio il proxy `GET /api/tenants/me` o le regole per `global_admin`.
- C'è un documento di riferimento? Abbiamo visto `MULTI_TENANT_IMPLEMENTATION.md` e `UI_HEADER.md` del Portal.

L'adeguamento in PrivacyPA lo facciamo noi.

### P2. Collegamento HelpDesk → PrivacyPA per il token di servizio

**Situazione.** `POST /api/service-auth/token` accetta già `tenant_id` e `audience`. Con `audience` il token è destinato a una sola app, dura 15 minuti e richiede un `ModuleLink` attivo dal mittente al destinatario (OP07). È il meccanismo giusto per le chiamate dall'HelpDesk a PrivacyPA.

**Richiesta.**

- Creare o dichiarare il collegamento `pramaia-helpdesk` → `PramaIA-PrivacyManager`, con scopo "richieste di accesso".
- Il campo `path` del collegamento viene controllato da qualcuno? L'HelpDesk chiama due prefissi: `/api/privacypa/access-governance` e `/api/privacypa/authorizations`. Ci serve sapere se basta un collegamento o ne servono due.
- Confermate che `PramaIA-PrivacyManager` è l'`app_id` con cui PrivacyPA è registrato nel Portal.

### P3. Anagrafica delle persone

**Situazione.** Nella valutazione PrivacyPA deve capire se la persona a cui si chiede l'accesso esiste ed è membro del tenant. Se non è riconoscibile, l'esito è `UNKNOWN`. Oggi PrivacyPA conosce le persone solo come `sub` del Portal e non ha un'anagrafica locale. Abbiamo visto che `GET /api/tenants/{tenant_id}/users` accetta i token di servizio (`require_admin_or_service`).

**Richiesta.**

- È questo l'endpoint di riferimento per verificare con un token di servizio che un utente esiste ed è membro del tenant? Distingue gli utenti disattivati?
- Qual è lo schema consigliato per tenere un'anagrafica locale sincronizzata? Lettura periodica, eventi del Portal sulle modifiche degli utenti, oppure un'app da prendere come esempio?
- `POST /api/users/batch-info` accetta i token di servizio e filtra per tenant?
- Va abilitato qualcosa perché PrivacyPA, con il proprio token di servizio, possa leggere gli utenti di ogni tenant (`UserAppPermission` / `GroupAppPermission`)?

### P4. Moduli di licenza di PrivacyPA

**Situazione.** Il Portal costruisce il catalogo dei moduli chiamando `GET /api/license/modules` su ogni app (`/api/licenses/modules-catalog`). PrivacyPA non espone quell'endpoint e oggi non controlla i moduli della licenza. Le specifiche chiedono 403 `MODULE_NOT_LICENSED` sulle API delle richieste di accesso quando il modulo non è concesso.

**Richiesta.**

- Quali codici contengono oggi le licenze di PrivacyPA in `entitlements.modules`? Le licenze esistenti devono continuare a funzionare.
- Concordiamo i codici. Proponiamo lo schema dell'HelpDesk:
  - un modulo generico `privacypa` che copre le funzioni attuali;
  - un modulo `access_governance`, non compreso in quello generico, per le API dedicate all'HelpDesk.

## Richieste al team HelpDesk

### H1. Token di servizio legato a tenant e destinatario

**Situazione.** `privacypa_client._portal_service_token` riusa il token delle licenze, che si chiede senza `tenant_id` né `audience`. Così il token non contiene il tenant e PrivacyPA risponde 400 a ogni chiamata ("tenant_id mancante nel token"). La cache dei token è indicizzata solo per `app_id`.

**Richiesta.**

- Chiedere il token con `tenant_id` = tenant del ticket e `audience` = `PramaIA-PrivacyManager`.
- Indicizzare la cache per `(tenant_id, audience)`, rispettando la scadenza di 15 minuti.
- Continuare a inviare `X-Tenant-ID`: PrivacyPA risponderà 403 se non coincide con il tenant del token.

### H2. Identificativo della persona

**Situazione.** In `AccessRequestFields.tsx` la persona si salva con `String(option.id || option.email)`. Quando manca l'id, a PrivacyPA arriva un'email al posto dell'id utente del Portal. I dati di PrivacyPA (formazione, ruoli, autorizzazioni) sono indicizzati per id utente, quindi con un'email il controllo `TRAINING_VALID` darebbe un esito falso e l'autorizzazione verrebbe registrata su un identificativo diverso.

**Richiesta.** Inviare `subject_user_id` solo quando è l'id utente del Portal. Se c'è solo il nome, inviare `null`: PrivacyPA risponde `UNKNOWN`, come previsto.

### H3. Webhook con più tenant

**Situazione.**

- L'HelpDesk ha un solo segreto per i webhook di PrivacyPA (`privacypa_webhook_secret`). In PrivacyPA ogni tenant registra il proprio endpoint e riceve un segreto diverso: con due o più tenant la verifica della firma fallisce.
- Il gestore di `authorization.expired_not_revoked` cerca il ticket solo per numero, senza filtro sul tenant.

**Richiesta.**

- Segreto per tenant: per esempio una chiave per tenant nelle impostazioni, scelta in base al `tenant_id` della busta dell'evento.
- Filtro per `tenant_id` nella ricerca del ticket.

### H4. Conferma del contratto di `GET /assessments/{id}`

Per rileggere la decisione l'HelpDesk usa il campo `approval`, che nelle specifiche non è descritto. L'abbiamo implementato così:

```json
"approval": {
  "id": "…",
  "state": "PENDING | DECIDED | CANCELLED",
  "decision": "APPROVED | REJECTED | CHANGES_REQUESTED | null",
  "entity_version": 3,
  "notes": "…",
  "approver_id": "…",
  "requested_at": "…",
  "decision_at": "…"
}
```

`entity_version` è la `request_version` inviata dall'HelpDesk; senza revisione il campo vale `null`. Confermateci che va bene. Il contratto completo è in `INTEGRAZIONE_HELPDESK.md` nel repository di PrivacyPA.

## Cosa abbiamo già adeguato in PrivacyPA (nessuna modifica richiesta)

- **Revoca:** accettiamo sia `reason` (il campo storico) sia `revoked_reason` (quello che invia l'HelpDesk), più `revocation_reference`.
- **Modifica di un'autorizzazione:** il PATCH è parziale e `user_id` è facoltativo. Prima era obbligatorio e il PATCH dell'HelpDesk, che non lo invia, avrebbe ricevuto 422.
- **Registrazioni ripetute oltre le 24 ore** (durata dell'`Idempotency-Key`): se una concessione con stesso ticket, persona e profilo è già registrata, restituiamo il record esistente con 200 invece di un errore. Così l'HelpDesk non la segna come `FAILED`.
- **Permessi del ruolo di servizio:** comprendono la lettura del registro (`GET /authorizations/{id}`), che all'HelpDesk serve per l'`ETag`. Il ruolo non vede nessun altro modulo di PrivacyPA e non può modificare i profili.
- **Regola sugli accessi scaduti e non revocati:** era già presente per tutte le autorizzazioni. L'abbiamo estesa con 3 giorni di tolleranza e il webhook `authorization.expired_not_revoked` per quelle di origine HelpDesk.
- **Moduli di licenza:** `GET /api/license/modules` pubblica la proposta di P4 (`privacypa` generico, `access_governance` opzionale). Se il Portal sceglie codici diversi basta cambiarli in un punto.
- **Configurazione dell'installazione:** se l'HelpDesk è in rete locale o raggiungibile in http, PrivacyPA deve essere configurato con `WEBHOOK_ALLOWED_HOSTS` (o `WEBHOOK_ALLOW_PRIVATE_NETWORKS`) e `WEBHOOK_ALLOW_HTTP`, altrimenti la protezione SSRF blocca le consegne. È scritto in `INTEGRAZIONE_HELPDESK.md`.

## Riepilogo

| # | Team | Richiesta | Blocca l'integrazione? |
| --- | --- | --- | --- |
| P1 | Portal | Conferma della convenzione `X-Tenant-ID` / `tenants` | Sì, per chi lavora su più tenant |
| P2 | Portal | `ModuleLink` HelpDesk → PrivacyPA, uso del campo `path` | Sì |
| P3 | Portal | Endpoint e schema di sincronizzazione dell'anagrafica | No: senza, l'esito `UNKNOWN` si basa solo sull'id mancante |
| P4 | Portal | Codici dei moduli di licenza di PrivacyPA | Sì, per il 403 `MODULE_NOT_LICENSED` |
| H1 | HelpDesk | Token con `tenant_id` e `audience` | Sì: oggi ogni chiamata riceve 400 |
| H2 | HelpDesk | Solo id del Portal come `subject_user_id` | No, ma evita registrazioni errate |
| H3 | HelpDesk | Segreto del webhook per tenant e filtro sul tenant | Sì, con più di un tenant |
| H4 | HelpDesk | Conferma del campo `approval` | No |
