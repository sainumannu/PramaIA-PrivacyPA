# PrivacyPA — analisi della «Richiesta connettori VideoPA» e risposta

**Riferimento:** `RICHIESTA_CONNETTORI.md` del progetto VideoPA (21 settembre 2026)
**Stato del documento:** bozza interna. La parte 1 è per noi (team PrivacyPA, DPO): cosa implementare e cosa decidere. La parte 2 è il testo da inoltrare a VideoPA/ICT dopo che avremo chiuso le decisioni della sezione 1.5. Ciò che non sappiamo (URL, ambienti, referente, tempi) è segnato **[da compilare]**: non l'ho inventato.

---

## Sintesi

- Dei quattro connettori descritti, **uno riguarda noi: il C (PrivacyPA)**. A (snapshot VMS), B (hold sul VMS) e D (monitoring) sono tra VideoPA, VMS e ICT. Da noi ci si aspetta solo di non essere un collo di bottiglia per il DPO.
- **Il contratto di lettura che VideoPA ha già scritto (C1) non corrisponde a PrivacyPA reale.** Nel loro codice: `GET /api/processing-activities/{id}` con una API key e i campi `name`, `dpia_id`, `dpia_version`, `notice_id`, `notice_version`, `processor`, `retention_days`. Da noi: prefisso `/api/privacypa`, autenticazione con JWT del Portal (nessuna API key), `title` invece di `name`, DPIA e informative in endpoint separati e in numero anche maggiore di uno, e **nessun campo `retention_days`** (la conservazione è a regole, per categoria di dati, anche «a evento» o «permanente»).
- **Per gli eventi tecnici (C2) non esiste ancora nulla in PrivacyPA**: nessun endpoint che riceva eventi da sistemi esterni. Va costruito, ed è il lavoro più grande.
- Servono **4 lavori obbligatori** (W1–W4 sotto) e 2 facoltativi. Prima di scrivere codice vanno chiuse **6 decisioni** (1.5); la più importante è come si ricava «i giorni di conservazione» del video.

---

## Parte 1 — Interna: cosa dobbiamo implementare

### 1.1 Cosa ci riguarda del documento

| Sezione | Riguarda PrivacyPA? | Perché |
|---|---|---|
| §3 Connettore A — snapshot VMS | No | VMS → VideoPA |
| §4 Connettore B — hold sul VMS | No | VideoPA → VMS. Le «richieste Autorità» non sono un modulo di PrivacyPA |
| **§5 Connettore C — PrivacyPA** | **Sì** | C1 (VideoPA legge da noi) e C2 (VideoPA ci notifica) |
| §6 Connettore D — monitoring | No | |
| §2 domande 3 e 8 | Sì | URL/autenticazione di PrivacyPA; account di servizio |
| §7 Vincoli comuni | Sì | JWT del Portal, tenant, idempotenza, degrado, dati riservati |

### 1.2 Stato reale di PrivacyPA rispetto a quanto VideoPA assume

Verificato sul nostro codice.

| VideoPA assume | PrivacyPA oggi | Esito |
|---|---|---|
| `GET /api/processing-activities/{id}` | `GET /api/privacypa/processing-activities/{id}` | Percorso diverso |
| `Authorization: Bearer {PRIVACYPA_API_KEY}` | Solo JWT del Portal (HS256, `iss = pramaia-portal`) o token dell'IdP dell'ente. **Non esistono API key.** I permessi vengono dalle **assegnazioni di ruolo** dell'utente (`sub`) nell'ente | Serve un account di servizio con un ruolo assegnato |
| `name` | `title` (più `code`, es. `TR-000123`) | Equivalente |
| `dpia_id`, `dpia_version` | Nessun campo sul trattamento. Le DPIA sono a parte: `GET /api/privacypa/dpias` (filtro solo per stato: si filtra per `processing_activity_id` lato client). Possono essere **più di una** per trattamento. `version_no` **cresce a ogni modifica**, non solo alle approvazioni | Equivalente con avvertenze |
| `notice_id`, `notice_version` | Il dettaglio del trattamento ha `notices[]` (id, codice, titolo, stato: **più informative**). La versione sta in `GET /notices/{id}` (`version_no`, `version_label`, `effective_from`, `published_url`) | Equivalente, richiede chiamate in più |
| `processor` | `processors[]` (collegamenti a soggetti, con atto art. 28 opzionale); il nome sta in `GET /parties/{id}`. **Più responsabili** possibili | Equivalente, è una lista |
| `retention_days` | **Non esiste.** `retention_rules[]` con `retention_unit` (giorni, mesi, anni, a evento, permanente), `retention_value`, categoria di dati e classe documentale | **Manca**: va definito |
| `PRIVACYPA_URL` senza tenant | Il tenant è quello del claim `tenant_id` del token; un id di un altro ente risponde «non trovato» | Coerente con §7.2 |

Altre caratteristiche utili da conoscere:

- **Nessuna scrittura da parte di VideoPA è oggi possibile**: non esiste un endpoint che riceva eventi da sistemi esterni.
- Il nostro **outbox** (`GET /api/privacypa/outbox`, solo amministratori) emette *i nostri* eventi; non c'entra con l'outbox di VideoPA.
- Da poco abbiamo i **webhook in uscita firmati** (`WEBHOOKS.md`): PrivacyPA può spingere eventi verso un endpoint HTTPS (`processing.updated`, `dpia.review_due`, …). Utile per tenere vivo lo snapshot di VideoPA senza sondare in continuo.
- `Idempotency-Key` è già gestita sulle POST JSON (8–128 caratteri). Limite di richieste: 600 al minuto per indirizzo client, in memoria (con più istanze va affiancato dal reverse proxy).
- Gli elementi possono avere un **livello di riservatezza** (DPIA, documenti, violazioni, progetti). Un account di sola lettura vede solo ciò che il suo ruolo consente: per le DPIA riservate lo snapshot non può dire di più.

### 1.3 Lavori da fare

Priorità come nel documento VideoPA (tutto ciò che riguarda il DPO è priorità 1). Le stime sono grossolane, da rivedere dopo le decisioni.

#### W1 — Accesso macchina-a-macchina e ruolo «integrazione» (piccolo–medio)

- Nuovo ruolo **`INTEGRATION`** per gli account di servizio, distinto da `READ_ONLY`: lettura dei moduli necessari (trattamenti, DPIA, informative, soggetti, regole di conservazione) e il solo permesso nuovo `integration.create` per inviare eventi. Non vede violazioni, richieste degli interessati, audit, riservatezza alta.
  - Perché non usare `READ_ONLY`: darebbe a *qualsiasi* utente di sola lettura la possibilità di inviare eventi se concedessimo il permesso di scrittura; e un override per ente vale per tutto il ruolo.
- Endpoint di verifica `GET /api/privacypa/integrations/ping`: risponde con tenant, ruolo dell'account e versione. Serve a chi configura il connettore.
- Procedura scritta per creare l'assegnazione di ruolo dell'account di servizio (la fa un amministratore dell'ente).
- **Dipende dalla Piattaforma PramaIA** (domanda 8 di VideoPA): chi emette il JWT dell'account di servizio, con quali claim (`sub` stabile, `tenant_id`, `iss = pramaia-portal`), con quale durata e come si rinnova. Noi validiamo il token, non lo emettiamo.
- Criteri di accettazione: account con `INTEGRATION` legge lo snapshot e invia eventi; non legge nient'altro; un altro tenant riceve «non trovato»; test di autorizzazione per ruolo (la matrice esistente si estende).

#### W2 — Contratto di lettura «snapshot» per VideoPA (medio)

Un endpoint dedicato invece di far comporre a VideoPA cinque chiamate: `GET /api/privacypa/integrations/processing-activities/{id}/snapshot`, più l'elenco con `?updated_since=` per il polling, e supporto a `If-None-Match` (ETag) per non ripetere risposte invariate.

Risposta proposta (solo lettura, dati minimi, nessun testo descrittivo):

```json
{
  "processing_id": "uuid stabile",
  "code": "TR-000123",
  "name": "Videosorveglianza urbana",
  "status": "ACTIVE",
  "updated_at": "2026-09-21T10:00:00",
  "as_of": "2026-09-21T10:15:02",
  "dpia": { "id": "…", "code": "DPIA-000004", "version": 3, "status": "APPROVED",
            "approved_at": "2026-03-01T09:00:00", "next_review_date": "2027-03-01" },
  "dpia_others": 0,
  "notices": [ { "id": "…", "code": "IN-000002", "status": "APPROVED", "version": 2,
                 "version_label": "v2", "effective_from": "2026-04-01", "published_url": "https://…" } ],
  "processors": [ { "party_id": "…", "name": "XYZ S.r.l.",
                    "agreement": { "id": "…", "status": "SIGNED", "valid": true, "expiry_date": "2027-12-31" } } ],
  "retention": {
    "days": 7,
    "kind": "FIXED",
    "rule_id": "…",
    "rules_total": 2,
    "rules": [ { "id": "…", "category": "IMMAGINI", "unit": "DAYS", "value": 7, "days": 7 } ]
  }
}
```

- `dpia`: la DPIA «di riferimento» (quella approvata più recente; se non c'è, la più recente); `dpia_others` dice quante altre esistono. Se l'account non può leggere una DPIA riservata: `"dpia": { "restricted": true }`, senza altri dettagli.
- `retention.days`: vedi decisione 1.5-A. Per regole a evento o permanenti: `days: null` e `kind` = `EVENT_BASED` / `PERMANENT`; VideoPA **non** deve aprire un `RETENTION_MISMATCH` automatico su un valore assente.
- Ogni risposta ha `as_of`: VideoPA calcola la propria «stantietà» (24 ore, come dichiarato) sull'ultimo sync riuscito, non sul nostro `updated_at`.
- Criteri di accettazione: test di contratto (schema stabile, campi obbligatori), isolamento tra enti, DPIA riservata, trattamento senza DPIA/informativa/regole, ETag/304, filtro `updated_since`, limite di 200 elementi a pagina.

#### W3 — Ricezione degli eventi tecnici da VideoPA (grande)

Endpoint `POST /api/privacypa/integrations/events` (fino a 100 eventi per chiamata), con `Idempotency-Key` e dedupe per `event_id`. Ogni evento:

```json
{
  "source": "VIDEOPA",
  "event_id": "uuid dell'evento nell'outbox di VideoPA",
  "type": "AudioEnabled",
  "occurred_at": "2026-09-21T09:58:00",
  "processing_id": "uuid del trattamento in PrivacyPA",
  "subject_ref": "CAM-0042",
  "summary": "Audio attivato sulla camera CAM-0042",
  "link": "https://videopa.ente.example/cameras/CAM-0042"
}
```

Cosa fa PrivacyPA (e cosa **non** fa: non approva, non cambia lo stato del trattamento, non decide nulla in autonomia):

1. Salva l'evento in una nuova tabella (migrazione 0007), con dedupe su ente + sorgente + `event_id`. Conserva solo tipo, riferimento della camera, riassunto (max 200 caratteri) e link: **niente coordinate, IP, credenziali, evidenze**.
2. Se il trattamento esiste nell'ente: apre una **segnalazione per il DPO** (nuova regola `EXTERNAL_CHANGE_REVIEW_PENDING`, gravità dal tipo di evento) e un **task** «Rivedere modifica tecnica da VideoPA», assegnato alla struttura del trattamento e, se indicato, al referente privacy. Più eventi dello stesso tipo sullo stesso trattamento **si accorpano** nella stessa segnalazione aperta (contatore), per non inondare il DPO se si spostano 50 camere.
3. Se il trattamento non esiste o è di un altro ente: risponde `UNMATCHED` (l'evento resta visibile all'amministratore per smistamento, senza rivelare nulla dell'altro ente).
4. La segnalazione si chiude quando il DPO **archivia** l'evento («nessun impatto», con nota) o registra la revisione del trattamento (approvazione con `last_review_date` successiva all'evento).
5. Risposta `202` con l'esito per evento: `ACCEPTED`, `DUPLICATE`, `UNMATCHED`, `REJECTED` (con motivo). Regola per il consumer di VideoPA: segna l'evento come consegnato per i primi tre; `REJECTED` è un errore permanente, non si ritenta.
6. Tutto è in audit e pubblicato sull'outbox (quindi arriva anche ai webhook, per esempio come `finding.opened`).

Proposta di trattamento per tipo di evento (da validare con il DPO, configurabile per ente nei parametri della regola):

| Evento VideoPA | Gravità | Motivo |
|---|---|---|
| `AudioEnabled` | Alta | Il sonoro è un trattamento ulteriore: informativa e probabile DPIA |
| `AnalyticsEnabled` (es. volto), eventi ANPR / lettura targhe | Alta | Dati biometrici o targhe: DPIA, base giuridica |
| `RetentionChanged` | Media | Confronto con la conservazione dichiarata |
| `VmsChanged` | Media | Responsabile o sub-responsabile, trasferimenti |
| `CameraFieldOfViewChanged`, `CameraMoved`, `PrivacyMaskChanged` | Media | L'area ripresa cambia; cartelli e informativa |
| `CameraInstalled`, `CameraDecommissioned` | Media | Aggiornare il registro e i cartelli |
| `ExternalAccessGranted` / `Revoked` | Media | Nuovi destinatari o comunicazioni a terzi |
| `PrivacyReviewRequested` | Media | Richiesta esplicita: crea sempre un task |

- Conservazione degli eventi ricevuti: proposta 24 mesi, poi eliminazione (parametro dell'ente).
- Criteri di accettazione: dedupe e idempotenza, isolamento tra enti, accorpamento, chiusura, eventi sconosciuti (`REJECTED` senza effetti), carichi da 100 eventi, nessun dato oltre i campi ammessi, audit, esclusione dall'export dei dati che non sono dell'ente.

#### W4 — Eventi in uscita per tenere vivo lo snapshot (piccolo)

Estendere il catalogo dei webhook con gli eventi che oggi non partono:

- `dpia.status_changed` (cambio di stato di una DPIA, incluse approvazioni),
- `notice.changed` (creazione, approvazione, ritiro di una versione di informativa),
- `retention.changed` (regole di conservazione di un trattamento).

VideoPA li può ricevere su un endpoint HTTPS e ricaricare lo snapshot del trattamento indicato (payload minimale: tipo, id, stato). **Condizione**: VideoPA deve esporre un ricevitore raggiungibile da PrivacyPA (indirizzo pubblico, oppure host consentito da configurazione; vedi `WEBHOOKS.md`) e verificare la firma. Se non è possibile, resta il polling con `updated_since` e ETag.

#### W5 (facoltativo, consigliato) — Contratto documentato e ambiente di prova (piccolo)

Documento del contratto con esempi reali, schema JSON degli endpoint W2/W3, un tenant di prova con qualche trattamento (con e senza DPIA, informativa, responsabile, regole di conservazione varie) e una suite di **test di contratto** condivisibile con VideoPA per evitare disallineamenti.

#### W6 (facoltativo) — Vista «Segnalazioni da sistemi esterni» (piccolo–medio)

Elenco per il DPO degli eventi ricevuti (per trattamento, gravità, stato) con azioni «archivia con nota» e «apri il trattamento». Se non lo facciamo, le segnalazioni compaiono comunque nella pagina delle verifiche e come task, ma senza una vista dedicata né lo smistamento dei `UNMATCHED`.

### 1.4 Ordine e dipendenze

1. **Subito, senza codice**: chiudere le decisioni 1.5 e la risposta di parte 2. W1 e W2 non possono partire prima di A e C.
2. W1 → W2 → W3. W4 e W5 in parallelo a W2/W3. W6 dopo W3.
3. Nessuno di questi lavori tocca ciò che esiste: sono endpoint e regole nuove, più una migrazione (0007) per gli eventi ricevuti.

### 1.5 Decisioni da prendere prima di scrivere codice

| # | Decisione | Proposta |
|---|---|---|
| **A** | Come si ricava «i giorni di conservazione» del video, visto che le regole sono per categoria di dati e possono essere a mesi, anni, a evento o permanenti | Indicare **una categoria di dati (o classe documentale) che identifica le riprese** (es. «Immagini di videosorveglianza»): lo snapshot usa la regola di quella categoria. Mesi e anni si convertono in giorni **a calendario** (a partire dalla data `as_of`, mai 30 giorni per mese o 365 per anno). Regole a evento o permanenti danno `days: null`. Serve conferma del DPO sulla categoria e che ogni trattamento di videosorveglianza ne abbia una |
| **B** | Più DPIA, più informative, più responsabili per trattamento: VideoPA ne assume una sola | Restituire liste; per la DPIA una «di riferimento» più il conteggio delle altre. VideoPA sceglie come mostrarle |
| **C** | Ruolo per gli account di servizio | Nuovo ruolo `INTEGRATION` (W1). Alternativa scartata: `READ_ONLY` |
| **D** | Chi deve ricevere il task quando arriva un evento | Struttura del trattamento + referente privacy (se indicato), a prescindere dalla gravità; il DPO lo vede tramite la segnalazione |
| **E** | Direzione degli eventi: VideoPA spinge o PrivacyPA legge l'outbox di VideoPA | **VideoPA spinge** verso di noi: non abbiamo credenziali né rete verso il suo database, la sua tabella è interna, e i ritentativi restano dove vive l'evento. Il consumer è suo, lo stesso principio della sua §4 |
| **F** | Riservatezza: uno snapshot può rivelare l'esistenza di una DPIA riservata? | Sì, come «riservata», senza dettagli (nessun titolo, nessuna versione) |

---

## Parte 2 — Da inoltrare a VideoPA / ICT

> **Oggetto:** Riscontro di PrivacyPA alla «Richiesta connettori — VideoPA» del 21/09/2026
>
> Grazie per la richiesta. Per la parte che ci riguarda (connettore C), premettiamo un'informazione importante: **il contratto di lettura indicato nel vostro §5 non coincide con quello di PrivacyPA**. Lo allineiamo con un endpoint dedicato (sotto) invece di farvi adattare cinque chiamate. Per gli eventi tecnici, oggi PrivacyPA non ha nessun punto di ricezione: lo realizziamo.

### 2.1 Risposte alle domande del §5

**1. Contratto reale: path, autenticazione, campi, versioning di DPIA e informativa**

- Base: `{PRIVACYPA_URL}/api/privacypa/…` (non `/api/…`).
- Autenticazione: **JWT del PramaIA Portal** (`iss = pramaia-portal`, claim `sub`, `tenant_id`). **Non esistono API key**: `PRIVACYPA_API_KEY` non è supportata. L'account di servizio deve avere un'assegnazione di ruolo nell'ente in PrivacyPA (lo crea un amministratore dell'ente; ruolo dedicato alle integrazioni, in arrivo).
- Endpoint di lettura per VideoPA (in arrivo, contratto in bozza qui sotto): `GET /api/privacypa/integrations/processing-activities/{processing_id}/snapshot` e l'elenco con `?updated_since=`, con `ETag` / `If-None-Match`.
- Campi rispetto alla vostra tabella:

| Campo atteso da VideoPA | In PrivacyPA | Note |
|---|---|---|
| `name` | `name` (titolo del trattamento) | sì, con questo nome nello snapshot |
| `dpia_id`, `dpia_version` | `dpia.id`, `dpia.version`, in più `status`, `approved_at`, `next_review_date` | Può esserci **più di una DPIA**: lo snapshot indica quella di riferimento e quante altre esistono. La versione cresce a ogni modifica, non solo alle approvazioni: per «quella in vigore» usate `status` e `approved_at` |
| `notice_id`, `notice_version` | `notices[]` con `id`, `version`, `version_label`, `status`, `effective_from`, `published_url` | **Più informative** per trattamento: è un elenco |
| `processor` | `processors[]` con nome e stato dell'atto art. 28 | **Più responsabili** possibili: è un elenco |
| `retention_days` | `retention.days` + `retention.kind` + regole | **Nuovo campo derivato.** Le regole di conservazione sono per categoria di dati e possono essere a mesi, anni, a evento o permanenti. Per le ultime due `days` è `null`: non aprite `RETENTION_MISMATCH` su un valore assente. Per la conversione serve una vostra risposta (domanda Q1) |

**2. Come si identifica un trattamento**

- `processing_id` = l'identificativo (UUID) del trattamento in PrivacyPA: **stabile per tutta la vita del trattamento** (le versioni sono istantanee separate, non nuovi identificativi). Lo stesso trattamento ha anche un `code` leggibile (`TR-000123`), utile da mostrare ma unico solo nell'ente.
- Le camere/siti li lega VideoPA ai trattamenti: PrivacyPA non conosce le camere. Ogni evento che ci inviate deve quindi riportare il `processing_id`.

**3. Dove depositare gli eventi dell'outbox**

- **Preferiamo che siate voi a spingerli** (non che PrivacyPA legga la vostra `outbox_events`): il consumer e i ritentativi restano dove vive l'evento.
- Endpoint (in arrivo): `POST /api/privacypa/integrations/events`, fino a 100 eventi per chiamata, con `Idempotency-Key` e dedupe su `event_id`. Formato: `source`, `event_id`, `type`, `occurred_at`, `processing_id`, `subject_ref`, `summary` (max 200 caratteri), `link`.
- Risposta `202` con l'esito per ogni evento: `ACCEPTED`, `DUPLICATE`, `UNMATCHED` (trattamento sconosciuto), `REJECTED` (errore permanente con motivo). Segnate come consegnati i primi tre; su `REJECTED` non ritentate. Per errori di rete o `5xx`, ritentate con lo stesso `event_id`.
- **Non inviate** coordinate, indirizzi IP, credenziali o evidenze: ci servono tipo, riferimento della camera, riassunto breve e link a VideoPA.
- Cosa facciamo con l'evento: lo registriamo, apriamo una segnalazione per il DPO e un task per la struttura del trattamento (eventi dello stesso tipo sullo stesso trattamento si accorpano). **PrivacyPA non approva né modifica nulla da sola**: la decisione resta al DPO, come richiedete.

**4. SLA**

- Lo snapshot è una lettura diretta e sempre attuale: ogni risposta riporta `as_of`. La soglia di 24 ore di «stantietà» è vostra e va calcolata sull'ultimo sync riuscito.
- Frequenza consigliata: una lettura completa notturna dei trattamenti collegati, più `updated_since` ogni 15–60 minuti. Limite attuale: 600 richieste al minuto per indirizzo client. Se preferite non fare polling, possiamo inviarvi i cambi come **webhook firmati** (`processing.updated`, e in arrivo `dpia.status_changed`, `notice.changed`, `retention.changed`) su un vostro endpoint HTTPS (domanda Q4).
- Disponibilità e tempi di ripristino di PrivacyPA: **[da compilare]**. Con PrivacyPA fuori servizio il vostro comportamento previsto (ultimo snapshot, `UNAVAILABLE`) è quello giusto.

**5. Allineamento tra `tenant_id` del Portal e l'ente in PrivacyPA**

- Nessuna mappatura: PrivacyPA usa **direttamente il claim `tenant_id` del token del Portal**. Se VideoPA e PrivacyPA sono sullo stesso Portal e sullo stesso ente, il valore coincide. Per ogni ente **[da compilare: tenant_id]**. Un identificativo di un altro ente riceve «non trovato», mai dati.

### 2.2 Decisioni del kickoff che ci riguardano (§2)

| # | Risposta di PrivacyPA |
|---|---|
| 3 | PrivacyPA è un **servizio separato** (backend FastAPI, immagine Docker, porta 8003 dietro il Portal). URL: **[da compilare]**. Autenticazione: JWT del Portal (vedi sopra) |
| 8 | Chi emette il JWT dell'account di servizio è la Piattaforma PramaIA. Da noi servono: `sub` stabile, `tenant_id`, `iss = pramaia-portal`, scadenza breve con rinnovo. Assegneremo all'account un ruolo dedicato alle integrazioni, solo per l'ente interessato |

### 2.3 Riscontro nel formato del §9

| Voce | Risposta |
|---|---|
| Prodotto, versione, ambiente | PrivacyPA 1.0.0. Ambienti test / produzione: **[da compilare]** |
| Documentazione API | Contratto in bozza in questa risposta; documentazione completa con esempi e schema JSON prima dell'avvio dello sviluppo (lavoro W5). Per gli endpoint attuali: Swagger disattivato di default, attivabile in test |
| Campi disponibili | Tabella del punto 1: tutti «equivalenti» tranne `retention_days` («nuovo, derivato») |
| Vincoli di rete e account di servizio | HTTPS. Account di servizio del Portal con ruolo di integrazione assegnato per ente. Se ci spingerete eventi da una LAN video o DMZ: indicateci gli indirizzi di uscita, da autorizzare sul reverse proxy. **[da compilare]** |
| Referente tecnico e disponibilità per il kickoff | **[da compilare: nome, contatto, disponibilità]** |

### 2.4 Domande che poniamo noi a VideoPA

| # | Domanda | Perché ci serve |
|---|---|---|
| **Q1** | Quale **categoria di dati** o classe documentale identifica le riprese (es. «Immagini di videosorveglianza»)? Ogni trattamento di videosorveglianza avrà un'unica regola per quella categoria? | Ricavare `retention.days` senza ambiguità |
| **Q2** | Come state calcolando `RETENTION_MISMATCH`: confrontate la retention del VMS con quella di PrivacyPA con «maggiore di»? Come trattate le conservazioni «a evento» o «permanenti»? | Non aprire falsi conflitti sul DPO |
| **Q3** | Esempio reale (anonimo) di ogni tipo di evento in uscita, con volume atteso al giorno e picco | Dimensionare, definire l'accorpamento |
| **Q4** | Potete esporre un ricevitore HTTPS per i **nostri** webhook (cambi di DPIA, informativa, conservazione)? Con quale indirizzo, raggiungibile da dove? Altrimenti facciamo solo polling | Decidere se sviluppare W4 |
| **Q5** | Un evento può riguardare più trattamenti (una camera che serve due finalità)? | Un `processing_id` per evento o un elenco |
| **Q6** | Cosa deve vedere VideoPA di una DPIA riservata: basta «esiste ma riservata»? | Conferma della decisione F |
| **Q7** | Le camere e i siti sono legati ai trattamenti dentro VideoPA: chi mantiene quel collegamento e cosa succede se un trattamento cambia struttura o viene archiviato? | Evitare eventi `UNMATCHED` |
| **Q8** | Tempi che vi servono per iniziare i connettori | Ordinare W1–W4 |

### 2.5 Cosa ci impegniamo a fare e cosa no

**Sì (con date da concordare: [da compilare]):** ruolo per gli account di servizio e `ping` (W1), snapshot di lettura (W2), ricezione eventi con segnalazione al DPO (W3), eventuali webhook per i cambi di DPIA, informativa e conservazione (W4), contratto documentato e ambiente di prova (W5).

**No:** PrivacyPA non prende decisioni giuridiche in automatico sugli eventi, non modifica trattamenti, DPIA o informative da sola, non legge il vostro outbox, non gestisce hold, monitoring o Vault, e non invia email (il promemoria via email resta di un prodotto esterno).

---

## Note finali per noi

- **Rischio principale**: se VideoPA implementa il connettore C1 sul contratto che ha scritto (senza la nostra risposta) legge un endpoint che non esiste. Va risposto prima che inizino.
- **Rischio di disallineamento sulla conservazione**: senza la decisione A, `RETENTION_MISMATCH` può segnalare falsi conflitti al DPO (regole a evento, più categorie) o non segnalarne veri.
- **Riservatezza**: gli eventi ricevuti non devono diventare un canale per far arrivare in PrivacyPA dati che non ci servono (coordinate, IP): lo si verifica in W3 con validazione dei campi.
- **Non provato**: nulla di questo è ancora stato realizzato; lo stato attuale (percorsi, campi, ruoli, limiti) è verificato sul codice, non su un'istanza in esecuzione.
