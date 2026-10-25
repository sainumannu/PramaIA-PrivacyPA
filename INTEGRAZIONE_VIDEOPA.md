# PrivacyPA ↔ VideoPA — contratto (W1, W2, W3)

Per chi riscrive `services/videopa/privacy.py`. Stato: **implementato e provato con test in-process**; non ancora provato da VideoPA né con un Portal reale. Lo stato di ciò che manca è in fondo (sezione 9).

## 1. Cosa cambia rispetto a quanto assumeva VideoPA

| VideoPA assumeva | Realtà |
|---|---|
| `GET /api/processing-activities/{id}` | `GET /api/privacypa/integrations/processing-activities/{id}/snapshot` |
| `Authorization: Bearer {API key}` | Solo JWT del Portal (HS256, `iss = pramaia-portal`, claim `sub` stabile e `tenant_id`). Non esistono API key |
| `retention_days` | `retention.days` + `retention.kind` (vedi 4). Può essere `null` |
| Un solo `dpia_id`, `notice_id`, `processor` | `dpia` (una «di riferimento» + `dpia_others`), `notices[]`, `processors[]` |

## 2. Come si crea l'account di servizio (lo fa un amministratore dell'ente)

1. Il Portal emette il JWT dell'account di servizio: `sub` stabile (es. `svc-videopa`), `tenant_id` dell'ente, `iss = pramaia-portal`, durata e rinnovo a cura del Portal (PrivacyPA valida il token, non lo emette).
2. In PrivacyPA: pagina **Organizzazione → Ruoli privacy** (o `POST /api/privacypa/roles/assign` con `{user_id, role_code: "INTEGRATION", scope_type: "TENANT"}`): assegnare al `sub` il ruolo **Integrazione**, con ambito «intero ente» (o `UNIT` con la struttura: allora vede solo i trattamenti di quella struttura). Il ruolo compare nel catalogo dopo il primo avvio del backend aggiornato.
3. Verifica: `GET /api/privacypa/integrations/ping` → `200` con `roles: ["INTEGRATION"]`.
4. **Configurare la categoria delle riprese** (decisione A): `PUT /api/privacypa/policy/integrations` con `{"retention_category": "<codice categoria dati o classe documentale>"}` e `If-Match` (solo amministratori). `ping` mostra `retention_category_configured`.

Il ruolo `INTEGRATION` **non vede nessun altro modulo** (trattamenti, DPIA, violazioni, audit, ricerca…: `403` o vuoto) e non può modificare nulla. I permessi `integration.*` non sono modificabili per ente.

## 3. Endpoint

Tutti richiedono `integration.view`. Tenant dal token; un id di un altro ente risponde `404`.

- `GET /api/privacypa/integrations/ping` → `{ok, contract_version: 1, tenant_id, subject, roles, tenant_admin, max_page: 200, retention_category_configured}`.
- `GET /api/privacypa/integrations/processing-activities/{id}/snapshot` → un snapshot (5). Intestazioni `ETag` e `Cache-Control: private, no-cache`; con `If-None-Match` uguale risponde `304` senza corpo.
- `GET /api/privacypa/integrations/processing-activities?updated_since=&status=&limit=&offset=` → `{items: [snapshot…], total, limit, offset, as_of}`. `limit` 1–200 (oltre: `422`). Ordinati per `updated_at`, poi id. `updated_since` è ISO 8601, **inclusivo**, con o senza fuso (senza = UTC): per il polling usare l'`updated_at` più alto già visto e scartare i doppioni con `etag`.

Un trattamento è invisibile (`404`, assente dall'elenco) se la sua struttura è fuori dall'ambito dell'account o il suo livello di riservatezza non ammette il ruolo `INTEGRATION`.

## 4. Conservazione (`retention`)

`days` è quello della regola del trattamento la cui **categoria di dati** (codice del catalogo) **o classe documentale** coincide, senza distinguere le maiuscole, con `retention_category` dell'ente. Mesi e anni sono convertiti in giorni **di calendario a partire da oggi** (`as_of`): «1 mese» dal 31 gennaio vale 28 giorni, «1 anno» attraversa gli anni bisestili. **Il valore cambia con la data**: non è un valore da confrontare tra giorni diversi, e l'`ETag` cambia di conseguenza.

| `kind` | `days` | Significato |
|---|---|---|
| `FIXED` | intero | Regola a giorni, mesi o anni |
| `EVENT_BASED` | `null` | La conservazione dipende da un evento |
| `PERMANENT` | `null` | Conservazione permanente |
| `NO_RULE` | `null` | Nessuna regola per quella categoria (o senza valore) |
| `AMBIGUOUS` | `null` | Più regole diverse per la stessa categoria: da correggere in PrivacyPA |
| `NOT_CONFIGURED` | `null` | L'ente non ha indicato la categoria delle riprese |

**VideoPA non deve aprire `RETENTION_MISMATCH` quando `days` è `null`**: un valore assente non è un valore diverso. Il confronto con la conservazione del VMS è una decisione di VideoPA (domanda Q2). `rules[]` elenca tutte le regole del trattamento (`id`, `category`, `record_class`, `unit`, `value`, `days`) per chi vuole mostrarle.

## 5. Forma dello snapshot

```json
{
  "processing_id": "…", "code": "TR-000123", "name": "Videosorveglianza urbana", "status": "ACTIVE",
  "updated_at": "2026-09-21T10:00:00", "as_of": "2026-09-21T10:15:02",
  "dpia": {"id": "…", "code": "DPIA-000004", "version": 3, "status": "APPROVED",
           "approved_at": "2026-03-01T09:00:00", "next_review_date": "2027-03-01"},
  "dpia_others": 0,
  "notices": [{"id": "…", "code": "IN-000002", "status": "APPROVED", "version": 2, "version_label": "v2",
               "effective_from": "2026-04-01", "published_url": "https://…"}],
  "processors": [{"party_id": "…", "name": "XYZ S.r.l.",
                  "agreement": {"id": "…", "status": "SIGNED", "valid": true, "expiry_date": "2027-12-31"}}],
  "retention": {"days": 7, "kind": "FIXED", "rule_id": "…", "rules_total": 1,
                "rules": [{"id": "…", "category": null, "record_class": "IMMAGINI", "unit": "DAYS", "value": 7, "days": 7}]},
  "etag": "\"…\""
}
```

- Date senza fuso, in UTC (`updated_at`, `as_of`, `approved_at`). `as_of` è il momento della lettura: la «stantietà» di VideoPA va calcolata sull'ultimo sync riuscito, non sul nostro `updated_at`.
- `dpia`: la approvata più recente; se non c'è, la più recente tra quelle non archiviate né «non necessarie»; altrimenti la più recente; `null` se non ce ne sono. **`version` cresce a ogni modifica, non solo alle approvazioni**: per «quella in vigore» usare `status` e `approved_at`. Se l'account non può leggere la DPIA per riservatezza: `{"restricted": true}` e nient'altro (decisione F).
- `notices`: tutte le informative collegate, anche ritirate (`status`). `version*`, `effective_from` e `published_url` sono della versione **approvata in vigore** (`null` se non ce n'è una).
- `processors[].agreement`: l'atto art. 28 collegato al trattamento; `valid` = firmato, in vigore e non scaduto **oggi**. `null` se non collegato.
- Nessun testo descrittivo: solo identificativi, codici, stati, date e i nomi dei responsabili.
- `etag`: hash del contenuto senza `as_of`. È lo stesso valore dell'intestazione.

## 6. Ricezione degli eventi tecnici da VideoPA (W3)

`POST /api/privacypa/integrations/events` (permesso `integration.create`: solo il ruolo `INTEGRATION` e gli amministratori). Corpo:

```json
{"events": [{
  "source": "VIDEOPA", "event_id": "uuid dell'evento nell'outbox di VideoPA", "type": "AudioEnabled",
  "occurred_at": "2026-09-21T09:58:00", "processing_id": "uuid del trattamento in PrivacyPA",
  "subject_ref": "CAM-0042", "summary": "Audio attivato sulla camera CAM-0042", "link": "https://videopa.ente.example/cameras/CAM-0042"
}]}
```

Da 1 a 100 eventi per chiamata (oltre: `422`, e nessun evento è salvato). Risposta `202` con l'esito **per evento**, nell'ordine inviato:

| Esito | Significato | Cosa fa il consumer VideoPA |
|---|---|---|
| `ACCEPTED` | Salvato e collegato al trattamento | Segna come consegnato |
| `DUPLICATE` | Stesso `event_id` già ricevuto (per ente e sorgente) | Segna come consegnato |
| `UNMATCHED` | Il trattamento non esiste nell'ente, o non è nell'ambito dell'account. Salvato per lo smistamento da parte di un amministratore; non si dice altro | Segna come consegnato |
| `REJECTED` + `reason` | Contratto violato (tipo o sorgente sconosciuti, data non valida o futura, testo troppo lungo, link non http/https, `event_id` mancante) | **Errore permanente: non ritentare** |

Con `Idempotency-Key` un ritentativo dell'intera chiamata restituisce la stessa risposta. Il `DUPLICATE` copre invece il ritentativo di singoli eventi.

**Tipi accettati** e gravità della segnalazione (proposta da validare con il DPO): alta — `AudioEnabled`, `AnalyticsEnabled`, `AnprEnabled`; media — `RetentionChanged`, `VmsChanged`, `CameraFieldOfViewChanged`, `CameraMoved`, `PrivacyMaskChanged`, `CameraInstalled`, `CameraDecommissioned`, `ExternalAccessGranted`, `ExternalAccessRevoked`, `PrivacyReviewRequested`. Un altro tipo è `REJECTED`: se VideoPA ne emette di diversi (per l'ANPR abbiamo assunto `AnprEnabled`), va concordato e aggiunto.

**Limiti dei campi:** `summary` 200 caratteri, `subject_ref` 100, `link` 500, `event_id` 100; `occurred_at` ISO 8601 (senza fuso = UTC) e non più di un giorno nel futuro. **Tutto il resto si ignora e non si conserva** (coordinate, IP, credenziali, evidenze, campi sconosciuti).

**Cosa fa PrivacyPA, e cosa non fa.** Non approva nulla, non cambia lo stato né i dati del trattamento, non valuta l'impatto. Per ogni gruppo *trattamento + tipo* di eventi non rivisti apre **una** segnalazione (regola `EXTERNAL_CHANGE_REVIEW_PENDING`) con un contatore: cinquanta camere spostate sono una segnalazione, non cinquanta. Apre inoltre **un task** «Rivedere modifica tecnica da VideoPA: <tipo>» assegnato alla struttura del trattamento e, se indicato, al referente privacy (alta priorità per i tipi di gravità alta). La segnalazione arriva anche ai webhook come `finding.opened`, e ogni evento ricevuto è sull'outbox come `external_event.received`.

**Chiusura.** La segnalazione si chiude quando: (a) il DPO archivia gli eventi con una nota (`POST /integrations/external-events/{id}/archive`, con `If-Match`, permesso `externalevent.edit`: amministratori, Privacy Manager, DPO); oppure (b) il trattamento viene rivisto con `last_review_date` **successiva al giorno** dell'evento (lo stesso giorno non basta, si archivia); oppure (c) la regola viene disattivata. `GET /integrations/external-events?status=&processing_id=&type=` elenca gli eventi nell'ambito dell'utente (`externalevent.view`); quelli `UNMATCHED` li vede solo chi ha visibilità sull'intero ente.

**Conservazione.** Gli eventi ricevuti si eliminano dopo 24 mesi (parametro dell'ente `external_events_retention_months`, 1–120, in `PUT /policy/integrations`); l'eliminazione avviene alla consegna successiva.

## 7. Le due domande a VideoPA

Q1 (quale categoria identifica le riprese) e Q2 (come calcolano `RETENTION_MISMATCH`) sono domande **a loro**: la risposta a Q1 va inserita in `retention_category` dell'ente; Q2 è la loro logica di confronto, ricordando che `days` può essere `null`.

## 8. Dati di prova

`python seed_demo_data.py` (idempotente) prepara nel tenant `001`:

- l'account di servizio **`svc-videopa`** con ruolo `INTEGRATION` e la categoria delle riprese **«Immagini di videosorveglianza»**;
- cinque trattamenti, uno per situazione dello snapshot:

| Trattamento | Stato | DPIA | Informativa | Responsabile | `retention` atteso |
|---|---|---|---|---|---|
| Videosorveglianza urbana | approvato | in corso | sì | Aruba, atto art. 28 valido | `FIXED`, 7 giorni |
| Videosorveglianza edifici comunali | approvato | no | no | no | `FIXED`, 1 mese di calendario (28–31 giorni) |
| Lettura targhe varchi ZTL | approvato | no | no | no | `EVENT_BASED`, `days: null` |
| Videosorveglianza scuole | approvato | no | no | no | `AMBIGUOUS` (regole da 7 e 30 giorni) |
| Fototrappole ambientali | bozza | no | no | no | `NO_RULE` |

- cinque eventi già ricevuti da VideoPA: `AudioEnabled` e due `CameraMoved` sulla videosorveglianza urbana (una segnalazione per tipo, la seconda con contatore 2, con i relativi task), `AnalyticsEnabled` sulle scuole e un evento `UNMATCHED` (trattamento inesistente).

Il JWT dell'account di servizio **non** è incluso: lo emette il Portal. Per provare in locale servono un token con `sub = svc-videopa` e `tenant_id = 001`.

## 9. Limiti noti

- `updated_since` si basa sulla data più recente tra trattamento, DPIA, informative, regole, responsabili e atti: **togliere un collegamento** (un'informativa o un responsabile dal trattamento) non la sposta se non tocca anche il trattamento. La verità è l'`ETag` dello snapshot; un sync completo periodico lo intercetta.
- L'elenco calcola gli snapshot di tutti i trattamenti visibili e poi filtra: va bene per centinaia di trattamenti, non è ottimizzato per decine di migliaia.
- Non provato: il ciclo completo con un JWT emesso dal Portal reale, e con un client VideoPA. PostgreSQL non provato.
- Eventi ricevuti: una revisione del trattamento chiude la segnalazione ma **non archivia gli eventi** (restano `PENDING` fino ai 24 mesi); i `UNMATCHED` non hanno ancora un'azione di smistamento (solo elenco e archiviazione); non c'è una schermata (solo API); segnalazioni e task compaiono perché le regole vengono rivalutate alla ricezione e a ogni apertura delle verifiche.
- Non ci sono ancora: eventi webhook `dpia.status_changed` / `notice.changed` / `retention.changed` (W4), vista per il DPO (W6), ambiente di prova con dati d'esempio (W5, in parte: `test_privacypa_integrations.py` e `test_privacypa_external_events.py` sono l'esempio eseguibile).
- La categoria delle riprese si imposta solo via API (nessuna schermata).
