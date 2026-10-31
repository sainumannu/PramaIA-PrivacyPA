# Risposte di Portal e HelpDesk sull'integrazione delle richieste di accesso

27 ottobre 2026 · team Portal e HelpDesk

Rispondiamo punto per punto a *Richieste a Portal e HelpDesk – richieste di accesso*. Abbiamo verificato ogni punto sul codice e sui database del Portal (sviluppo e installato). Alcune risposte hanno portato a modifiche, descritte in fondo. Sono nei repository e arrivano con le prossime versioni di Portal, HelpDesk, IntelliPA e SysAdmin.

## Risposte del team Portal

### P1. Convenzione multi-tenant

**La convenzione di riferimento non è quella che usava l'HelpDesk.** L'abbiamo cambiata, anche nell'HelpDesk.

Il problema dell'header: i ruoli del token (`tenant_role`, `apps`, `app_admin_for`) sono calcolati **solo per il tenant attivo** (`tenant_id`). Con `X-Tenant-ID` un utente sceglieva un altro tenant tra quelli in `tenants` e si portava dietro i ruoli del primo: un admin del tenant A che mandava `X-Tenant-ID: B` restava admin anche in B, dove era un semplice membro.

La convenzione adottata:

- **Un token = un tenant.** Il tenant su cui lavora la richiesta è il `tenant_id` del token.
- **Per cambiare tenant serve un nuovo token**: `POST /api/tenants/switch-tenant` sul Portal. Il Portal ricalcola i ruoli per il tenant scelto. Le app lo espongono con un proxy same-origin `POST /api/tenants/switch`.
- **`X-Tenant-ID` è solo un controllo**: se c'è, deve coincidere con il `tenant_id` del token, altrimenti 403. Non esiste più il valore `all`.
- **Elenco dei tenant per il selettore**: `GET /api/tenants/me/tenants` sul Portal, esposto dalle app come proxy `GET /api/tenants/me`.
- **Admin globale** (`roles` contiene `admin`, `tenant_role = "global_admin"`): può passare a qualunque tenant attivo anche senza esserne membro. Anche lui lavora in un tenant alla volta: niente vista "tutti i tenant".
- **Refresh**: il refresh token porta il tenant attivo, quindi `POST /api/auth/refresh` non riporta l'utente nel tenant predefinito.

Per PrivacyPA basta prendere dall'AppTemplate questi file:

- `backend/routers/tenants_router.py`
- `backend/services/portal_tenants_client.py`
- `frontend/src/services/tenantContext.tsx`

Il controllo che avevate previsto per i token di servizio (403 se `X-Tenant-ID` non coincide con il tenant del token) vale uguale per i token utente.

**Documenti:** abbiamo aggiornato la sezione "Convenzione per le app" in `MULTI_TENANT_IMPLEMENTATION.md` e il §3.6 di `UI_HEADER.md` nel repository del Portal. Il resto di `MULTI_TENANT_IMPLEMENTATION.md` descrive la prima implementazione ed è in parte superato: in caso di dubbio vale la sezione nuova.

### P2. Collegamento HelpDesk → PrivacyPA

- **`app_id`:** `PramaIA-PrivacyManager` è corretto, con le maiuscole. L'HelpDesk è `pramaia-helpdesk`.
- **Il collegamento oggi non esiste**, né nel Portal di sviluppo né in quello installato.
- **Il campo `path` non è controllato dal Portal.** Per emettere il token con `audience` serve solo un collegamento attivo `pramaia-helpdesk → PramaIA-PrivacyManager`, qualunque siano scopo e path. **Basta quindi un collegamento.**
  - `purpose` e `path` arrivano alle due app da `GET /api/platform/links`: PrivacyPA li trova in `incoming`. Se volete limitare i path accettati, il controllo è vostro.
  - Con un collegamento solo conviene usare il prefisso comune `/api/privacypa`.
- **Come crearlo:** dichiaratelo in `incoming_links_manifest` nella registrazione di PrivacyPA, per esempio:

  ```json
  [{"source_app_id": "pramaia-helpdesk", "purpose": "access_requests", "path": "/api/privacypa"}]
  ```

  Il Portal lo crea in automatico appena entrambe le app sono registrate. L'admin lo vede e può disattivarlo in "Collegamenti tra moduli", dove in alternativa si può crearlo a mano.

**Cosa verificare sul token che riceve PrivacyPA:**

| Claim / controllo | Valore atteso |
| --- | --- |
| `aud` | `PramaIA-PrivacyManager` |
| `token_type` | `service` |
| `sub` | `app:pramaia-helpdesk` |
| `app_id` | `pramaia-helpdesk` |
| `tenant_id` | il tenant della richiesta |
| `iss` | `pramaia-portal` |
| Durata | 15 minuti |

- Il Portal mette `tenant_id` nel token solo se l'HelpDesk è abilitato in quel tenant.
- Se il collegamento viene disattivato, i nuovi token vengono rifiutati e quelli già emessi scadono entro 15 minuti.

### P3. Anagrafica delle persone

- **Endpoint di riferimento:** `GET /api/tenants/{tenant_id}/users`, con il token di servizio di PrivacyPA. È quello senza `audience`: l'altro serve solo per chiamare un'altra app.
  - Restituisce: `id` (l'id utente del Portal), `email`, `display_name`, `is_active` (utente disattivato nel Portal), `role` (ruolo nel tenant), `is_default`, `joined_at`.
  - **Nuovo campo `membership_active`**: `false` se l'utente è stato tolto dal tenant ma non cancellato, per esempio dalla sincronizzazione AD. Per "esiste ed è membro" servono `is_active` e `membership_active` entrambi `true`.
- **Abilitazione:** non serve altro. Il Portal risponde se PrivacyPA è abilitato in quel tenant, cioè se almeno un utente o un gruppo del tenant ha il permesso sull'app PrivacyPA (`UserAppPermission` / `GroupAppPermission`). Altrimenti 403. È la stessa regola che decide se l'app compare agli utenti del tenant.
- **Sincronizzazione:** il Portal non emette eventi sulle modifiche degli utenti. Lo schema in uso nelle altre app è leggere l'elenco del tenant quando serve, con una cache breve per tenant. Esempi:
  - Communicator, `backend/services/portal_directory.py`;
  - RTDDirector, `backend/services/portal_directory_client.py`.

  Se vi serve un'anagrafica locale, conviene una lettura periodica più la lettura immediata quando arriva un id sconosciuto.
- **`POST /api/users/batch-info`: c'era un problema, l'abbiamo corretto.**
  - Prima accettava qualunque token e restituiva utenti di **tutti** i tenant, cercandoli per id o email.
  - Ora restituisce solo gli utenti membri attivi dei tenant del chiamante: con un token di servizio, il `tenant_id` del token (senza tenant risponde 400); con un token utente, i tenant di cui è membro; l'admin globale non ha filtro.
  - Restituisce anche `is_active`.

  Per verificare l'appartenenza al tenant usate comunque `GET /api/tenants/{tenant_id}/users`: `batch-info` non dice il ruolo né lo stato della membership.

### P4. Moduli di licenza di PrivacyPA

- **Nel Portal oggi non c'è nessuna licenza di PrivacyPA**, né in sviluppo né nell'installato, e il LicServer non ha un prodotto PrivacyPA. Non ci sono quindi codici esistenti da rispettare sul nostro lato. Se avete emesso licenze fuori da questo giro, verificate con chi le ha emesse cosa contengono in `entitlements.modules`.
- **Il Portal non interpreta i codici.** Il catalogo lo legge da `GET /api/license/modules` della vostra app, e la verifica la fa l'app. La proposta (`privacypa` per le funzioni attuali, `access_governance` separato per le API dell'HelpDesk) per noi va bene: i codici vanno concordati con chi emette le licenze nel LicServer.
- **Attenzione alle licenze senza moduli.** Nell'HelpDesk una lista `modules` vuota significa "nessun modulo concesso", non "tutti". Se PrivacyPA comincia a richiedere `privacypa`, una licenza emessa senza codici blocca tutto. Decidete esplicitamente come trattarla, per esempio richiedendo solo `access_governance` sulle API nuove.

### R1. Collegamento nelle installazioni dove PrivacyPA è già registrato

*Domanda del team HelpDesk in "Richieste dell'HelpDesk ad altri moduli – richieste di accesso".*

**Sì: rieseguendo `register_to_portal.py` il Portal crea il collegamento.** Il passo manuale nelle note di aggiornamento si può sostituire con "rieseguire `register_to_portal.py` con un token di admin del Portal".

- **Come funziona.** Se PrivacyPA è già registrato, il Portal risponde 409 e lo script invia `PATCH /api/apps/PramaIA-PrivacyManager` con il solo `incoming_links_manifest`. A ogni modifica di un'app il Portal crea i collegamenti mancanti del manifesto e riattiva quelli disattivati.
- **Condizione.** L'HelpDesk deve essere registrato e attivo nel Portal. Se si registra dopo, il collegamento nasce in quel momento.
- **Effetto da scrivere nelle note.** Rieseguire la registrazione riattiva il collegamento anche se un admin l'aveva disattivato apposta.

## Risposte del team HelpDesk

### H1. Token di servizio legato a tenant e destinatario

**Confermato, e corretto.** Prima `privacypa_client._portal_service_token` usava il token delle licenze, senza `tenant_id` né `audience`, con la cache per `app_id`. Ora:

- l'HelpDesk chiede il token con `tenant_id` = tenant del ticket e `audience` = `PramaIA-PrivacyManager`. L'`app_id` di destinazione si cambia con la variabile `PRIVACYPA_APP_ID`;
- la cache è per `(app_id, tenant_id, audience)` e dura fino alla scadenza, meno 60 secondi;
- `X-Tenant-ID` continua ad arrivare, sempre uguale al tenant del token.

Dipende dal collegamento di P2: finché non esiste, il Portal rifiuta la richiesta del token. L'HelpDesk lo segnala come "funzione non configurata", non come PrivacyPA offline.

### H2. Identificativo della persona

**Confermato, e corretto.** L'errore era in `AccessRequestFields.tsx`, dove c'era `String(option.id || option.email)`. Ora:

- **frontend:** `subject_user_id` è l'id utente del Portal preso dall'organigramma; con il solo nome è `null`;
- **API dell'HelpDesk:** un `subject_user_id` con un'email viene rifiutato (422);
- **richieste salvate prima della correzione:** se contengono un'email, a PrivacyPA arriva `null` invece dell'email, quindi l'esito è `UNKNOWN`.

### H3. Webhook con più tenant

**Confermato, e corretto.**

- **Un segreto per tenant.** L'HelpDesk legge `tenant_id` **al primo livello della busta** dell'evento e verifica la firma con il segreto di quel tenant. **Vi chiediamo quindi di mettere sempre `tenant_id` nella busta.**
- **Compatibilità.** Un tenant senza segreto proprio, o una busta senza `tenant_id`, usa ancora il segreto unico della versione precedente. Non appena un tenant ha il suo segreto, quello unico non vale più per lui.
- **Chi imposta il segreto.** Lo fa l'admin del tenant o l'admin dell'HelpDesk nel tenant, dalla scheda Automazione. URL di PrivacyPA e separazione dei compiti valgono per tutti i tenant e restano riservati all'admin del Portal.
- **Filtro per tenant.** Con `tenant_id` nella busta, ogni evento tocca solo i ticket di quel tenant: `access_approval.decided`, `authorization_profile.updated` e `authorization.expired_not_revoked`, cercato per numero di ticket. Il numero di ticket era già unico su tutti i tenant, quindi non c'erano collisioni, ma ora il filtro c'è comunque.

### H4. Campo `approval` di `GET /assessments/{id}`

**Va bene così.** L'HelpDesk legge:

- `decision`;
- `entity_version`: deve essere `null` o uguale alla versione della richiesta, altrimenti la decisione è ignorata;
- `notes`;
- `approver_id`.

`state` non lo usa, ma può restare. Con `decision: null` non cambia nulla. Potete aggiungerlo alle specifiche.

## Cosa è già cambiato da parte nostra

**Portal:**

- `switch-tenant`:
  - l'admin globale passa a qualunque tenant attivo;
  - i ruoli sono calcolati come al login, compresi quelli ereditati dai gruppi (prima no);
  - i token di servizio non sono accettati.
- Il refresh token mantiene il tenant attivo.
- Login, OAuth, refresh e cambio tenant costruiscono il token nello stesso modo. Prima il login con Microsoft non metteva il tenant nel token.
- `GET /api/tenants/me/tenants`: solo tenant e membership attivi; per l'admin globale tutti i tenant attivi.
- `POST /api/users/batch-info`: filtro per tenant e campo `is_active` (vedi P3).
- `GET /api/tenants/{tenant_id}/users`: campo `membership_active`.

**HelpDesk, IntelliPA, SysAdmin** (le app che usavano `X-Tenant-ID` per scegliere il tenant):

- Il tenant è quello del token; `X-Tenant-ID` diverso dà 403.
- Il selettore cambia tenant chiedendo un nuovo token.
- Sparita la vista "tutti i tenant" per l'admin globale.
- HelpDesk: le impostazioni condivise da tutti i tenant sono riservate all'admin del Portal (impostazioni generali, codici tenant, regole SLA, notifiche sugli eventi, URL di PrivacyPA). Il segreto dei webhook è per tenant (H3).
- IntelliPA: l'organigramma si legge solo per il tenant attivo.

## Riepilogo

| # | Esito | Azione |
| --- | --- | --- |
| P1 | Convenzione definita: un token per tenant, `X-Tenant-ID` solo come controllo | PrivacyPA: selettore e proxy come nell'AppTemplate |
| P2 | `app_id` confermati; il `path` non è controllato dal Portal, basta un collegamento | PrivacyPA: dichiararlo in `incoming_links_manifest` |
| P3 | `GET /api/tenants/{tenant_id}/users` con `membership_active`; nessun evento; `batch-info` corretto | PrivacyPA: lettura con cache per tenant |
| P4 | Nessuna licenza PrivacyPA esistente; i codici sono vostri | Concordare i codici con chi emette le licenze; decidere il caso "lista vuota" |
| R1 | Sì: rieseguendo `register_to_portal.py` il collegamento viene creato (o riattivato) | Note di aggiornamento: sostituire il passo manuale |
| H1 | Corretto: token con `tenant_id` e `audience`, cache per tenant | Funziona dopo il collegamento di P2 |
| H2 | Corretto: solo l'id del Portal, email rifiutate | — |
| H3 | Corretto: segreto per tenant, filtro per tenant | PrivacyPA: `tenant_id` sempre nella busta degli eventi |
| H4 | Contratto confermato | PrivacyPA: aggiungerlo alle specifiche |
