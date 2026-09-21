# Webhook di PrivacyPA (§70)

PrivacyPA invia gli eventi a un endpoint HTTPS esterno con una richiesta `POST` firmata. Il contenuto è minimale: tipo e id
dell'elemento, stato e date utili. Testi, titoli e persone non viaggiano mai nei webhook: chi riceve legge il resto dall'API.

Gli endpoint si gestiscono dalla pagina **Webhook** (solo amministratori) o da `/api/privacypa/webhooks`.

## Eventi

| Evento | Quando |
|---|---|
| `processing.updated` | Un trattamento è creato, modificato, cambia stato o relazioni |
| `processor.expiring` | Un atto art. 28 o un contratto scade a breve (regole `AGREEMENT_EXPIRING`, `CONTRACT_EXPIRING`) |
| `task.overdue` | Un task ha superato la scadenza (regola `TASK_OVERDUE`) |
| `breach.created` | È registrata una violazione dei dati |
| `dpia.review_due` | Una DPIA va rivista (regola `DPIA_REVIEW_DUE`) |
| `document.expiring` | La versione corrente di un documento scade entro 30 giorni o è scaduta (regola `DOCUMENT_EXPIRING`) |
| `finding.opened` | Si apre una verifica di conformità, qualunque regola |
| `webhook.ping` | Solo la prova manuale; non ci si iscrive |

Gli eventi di scadenza nascono dalle **verifiche di conformità**: PrivacyPA non ha un orologio proprio, quindi vanno
rivalutate periodicamente con `POST /api/privacypa/findings/evaluate` (per esempio una volta al giorno dal prodotto di
pianificazione). Ogni condizione genera l'evento una volta sola, finché non si risolve e si ripresenta.

## Richiesta

```http
POST /il-tuo-endpoint HTTP/1.1
Content-Type: application/json
User-Agent: PrivacyPA-Webhook/1.0
X-PrivacyPA-Event: task.overdue
X-PrivacyPA-Delivery: 6b1f0c1e-…            # chiave di idempotenza, uguale a ogni ritentativo
X-PrivacyPA-Timestamp: 1790000000
X-PrivacyPA-Signature: v1=3c9f…             # HMAC-SHA256 esadecimale di "<timestamp>.<corpo>"

{"data":{"entity_id":"…","entity_type":"task","rule_code":"TASK_OVERDUE"},"event":"task.overdue","id":"6b1f0c1e-…","occurred_at":"2026-09-20T18:00:00","tenant_id":"ente-a"}
```

`data.entity_type` può essere `processing_activity`, `processor_agreement`, `contract`, `task`, `data_breach`, `dpia`,
`document`, `compliance_finding`; `data` porta inoltre, quando ci sono, `status` e le date utili (`aware_at`, `due_date`…).
Il corpo è JSON con chiavi in ordine alfabetico e senza spazi: verificare la firma sui **byte ricevuti**, non su un JSON
riserializzato.

## Verificare la firma

1. Leggere `X-PrivacyPA-Timestamp` e rifiutare la richiesta se differisce dall'ora attuale di più di 5 minuti (replay).
2. Calcolare `HMAC-SHA256(segreto, timestamp + "." + corpo)` e confrontarlo **a tempo costante** con la firma dopo `v1=`.
3. Trattare `X-PrivacyPA-Delivery` come chiave di idempotenza: la consegna è «almeno una volta».
4. Rispondere `2xx` in fretta (timeout 10 s) ed elaborare dopo.

```python
import hashlib, hmac, time

def verify(secret: str, timestamp: str, body: bytes, signature: str, tolerance: int = 300) -> bool:
    if abs(time.time() - int(timestamp)) > tolerance:
        return False
    expected = "v1=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

```js
const crypto = require('crypto');
function verify(secret, timestamp, body /* Buffer */, signature, tolerance = 300) {
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > tolerance) return false;
  const expected = 'v1=' + crypto.createHmac('sha256', secret).update(`${timestamp}.`).update(body).digest('hex');
  return expected.length === signature.length && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
}
```

Il segreto (`whsec_…`) si vede **solo alla creazione** dell'endpoint o alla rotazione (`POST …/rotate-secret`: il vecchio smette
di valere subito). È cifrato a riposo e non compare in export, audit né log.

## Consegna e ritentativi

- Una risposta `2xx` conferma la consegna. Qualsiasi altra risposta, un timeout o un errore di rete la fanno ritentare
  dopo 1 min, 5 min, 30 min, 2 h, 12 h; al sesto tentativo fallito la consegna diventa **fallita** (riprovabile a mano).
- `410 Gone` disattiva subito l'endpoint. Dieci consegne fallite di seguito lo disattivano ("troppi errori"); si riattiva
  dalla pagina, azzerando il conteggio.
- Le redirezioni non si seguono.
- Le consegne nascono nella stessa transazione della modifica che le genera: se la modifica viene annullata non esiste nessun evento.
- L'ordine di arrivo non è garantito.

## Sicurezza della destinazione

L'URL deve essere `https://`, senza credenziali, e l'host deve risolvere solo a indirizzi pubblici (il controllo si ripete a
ogni invio). Per destinatari in rete interna l'amministratore del sistema può configurare:

| Variabile | Effetto |
|---|---|
| `WEBHOOK_ALLOWED_HOSTS` | Host (separati da virgola) ammessi anche se privati, es. `promemoria.ente.local` |
| `WEBHOOK_ALLOW_PRIVATE_NETWORKS=true` | Ammette qualsiasi indirizzo privato: usare solo se nessun ente non fidato può creare endpoint |
| `WEBHOOK_ALLOW_HTTP=true` | Ammette anche `http://` (solo sviluppo) |
| `PRIVACYPA_SECRETS_KEY` | Chiave per cifrare i segreti (in mancanza si usa `PRAMAIA_JWT_SECRET`). Se cambia, i segreti esistenti vanno rigenerati |
| `WEBHOOK_WORKER=false` | Spegne il ciclo in background; le consegne si inviano con `POST /api/privacypa/webhooks/dispatch` |
| `WEBHOOK_INTERVAL_SECONDS` | Ogni quanto il worker cerca consegne dovute (default 5) |

Con più istanze dell'applicazione ogni consegna viene «presa in carico» con un aggiornamento condizionato, quindi non parte due volte in parallelo.
