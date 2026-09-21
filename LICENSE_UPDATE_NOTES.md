# ✅ Sistema Licenze - Aggiornamento a Modello Portal Centrale

## 📋 Riepilogo Modifiche

L'implementazione del sistema di verifica licenze è stata aggiornata per conformarsi alla **specifica tecnica rev. 27/03/2026** con integrazione **Portal centrale**.

---

## 🔄 Cosa è Cambiato

### Modello Precedente (Deprecato)
```
❌ App riceveva file licenza manualmente (license.jwt + license_public_key.pem)
❌ Verifica firma locale con chiave pubblica
❌ Nessun sync automatico
❌ Nessun fallback centralizzato
```

### Nuovo Modello (Attuale)
```
✅ Admin carica licenza nel Portal per app_id
✅ App sync periodico dal Portal con autenticazione service-to-service
✅ Copia locale come fallback offline
✅ Gestione centralizzata licenze per tutte le app
✅ Update automatico licenza senza rideploy app
```

---

## 📁 File Modificati/Aggiunti

### Backend

**Nuovi:**
- `backend/services/portal_license_client.py` - Client Portal per sync licenza
- `backend/services/license_service.py` - **Riscritto** per usare modello Portal

**Modificati:**
- `backend/routers/license_router.py` - Aggiunto endpoint `/sync`, campi extra in response
- `backend/main.py` - Sync licenza all'avvio invece di solo verifica

### Frontend

**Modificati:**
- `frontend/src/components/LicensePanel.tsx` - Mostra checksum, last_sync, sync_status

### Configurazione

**Modificati:**
- `.env.template` - Aggiunto `APP_CLIENT_SECRET`, `DEPLOYMENT_TYPE`
- `.gitignore` - Rimosso `license.jwt`, `*.pem`; aggiunto `license_cache.json`
- `requirements.txt` - Già presente `httpx` (nessuna modifica necessaria)

### Documentazione

**Nuovi:**
- `LICENSE_SYSTEM_README.md` - Guida rapida nuovo modello

**Modificati:**
- `README.md` - Aggiornato sezione licenze

**Rimossi:**
- `generate_test_license.py` - Non più necessario (licenza dal Portal)
- `license_public_key.pem.example` - Non più necessario
- `IMPLEMENTATION_LICENSE_SYSTEM.md` - Sostituito da LICENSE_SYSTEM_README.md

---

## 🔧 Configurazione Richiesta

### 1. Variabili d'Ambiente (`.env`)

**Aggiungere:**
```env
# Service-to-Service Auth per License Sync
APP_CLIENT_SECRET=<genera-nel-portal>

# Deployment type
DEPLOYMENT_TYPE=on_prem
```

**Già presenti (verificare):**
```env
PORTAL_URL=http://localhost:3080
APP_ID=PramaIA-PrivacyManager
PRAMAIA_JWT_SECRET=<condiviso-portal>
```

### 2. Generare Credenziali nel Portal

**Step:**
1. Login Portal come Admin
2. Vai a **Settings → Apps**
3. Trova app: `pramaia-PramaIA-PrivacyManager`
4. Click **Generate Service Credentials**
5. Copia `client_secret` (visibile solo una volta!)
6. Incolla in `.env` → `APP_CLIENT_SECRET=...`

### 3. Upload Licenza nel Portal

**Step:**
1. Login Portal come Admin
2. Vai a **Settings → Licenses**
3. Click **Upload License**
4. Seleziona app: `pramaia-PramaIA-PrivacyManager`
5. Upload file `.jwt` firmato
6. Portal verifica e salva

**✅ L'app sincronizzerà automaticamente al prossimo avvio**

---

## 🚀 Flusso Sync Licenza

```
┌─────────────────┐
│ 1. Startup App  │
└────────┬────────┘
         │
         ↓
┌─────────────────────────────────────┐
│ 2. Autentica con Portal             │
│    POST /api/service-auth/token     │
│    {app_id, client_secret}          │
└────────┬────────────────────────────┘
         │
         ↓
┌─────────────────────────────────────┐
│ 3. Controlla status licenza         │
│    GET /api/licenses/apps/{id}/status│
└────────┬────────────────────────────┘
         │
         ├─────────────────────┐
         │                     │
         ↓                     ↓
┌────────────────┐   ┌───────────────────┐
│ Checksum       │   │ Checksum cambiato │
│ invariato      │   │ o prima volta     │
│                │   │                   │
│ Usa cache      │   │ Download payload  │
│ locale         │   │ /current          │
└────────────────┘   └─────────┬─────────┘
                               │
                               ↓
                     ┌─────────────────────┐
                     │ Salva license_      │
                     │ cache.json          │
                     └──────────┬──────────┘
                                │
         ┌──────────────────────┴────────────────┐
         │                                       │
         ↓                                       ↓
┌────────────────┐                    ┌──────────────────┐
│ 4. Verifica    │                    │ 4a. Verifica     │
│    payload     │                    │     payload      │
│                │                    │                  │
│ - Date         │                    │ - Date           │
│ - Ambiente     │                    │ - Ambiente       │
│ - Versione     │                    │ - Versione       │
│ - Moduli       │                    │ - Moduli         │
│ - Limiti       │                    │ - Limiti         │
└───────┬────────┘                    └────────┬─────────┘
        │                                      │
        └──────────────┬───────────────────────┘
                       │
                       ↓
              ┌────────────────┐
              │ 5. Cache in    │
              │    memoria     │
              │    (TTL 5 min) │
              └────────────────┘
```

### Sync Periodico (Raccomandato)

**Frequenza:** 15-60 minuti

**Implementazione futura:** Background task/scheduler

---

## 📊 Nuovi Campi Response API

### GET /api/license/status

**Campi aggiunti:**
```json
{
  "payload_checksum": "abc123def456...",
  "last_sync_at": "2026-03-27T14:30:00Z",
  "sync_status": "ok | portal_unreachable | using_cache"
}
```

### POST /api/license/sync

**Nuovo endpoint** per forzare sync manuale dal Portal.

---

## 🔍 Fallback Offline

### File Cache Locale

**Path:** `license_cache.json` (root progetto)

**Formato:**
```json
{
  "app_id": "pramaia-app",
  "license_id": "LIC-PA-2026-0142",
  "payload_checksum": "abc123...",
  "last_sync_at": "2026-03-27T14:30:00Z",
  "expires_at": "2027-03-31",
  "payload": { /* payload completo licenza */ }
}
```

### Regole Fallback

**Fallback offline è consentito solo se:**
1. ✅ Esiste cache locale (`license_cache.json`)
2. ✅ Licenza non scaduta (`expires_at >= now`)
3. ✅ Controlli ambiente/versione passano

**Altrimenti:**
❌ App blocca funzionalità con errore esplicito (`LICENSE_MISSING`, `LICENSE_EXPIRED`)

---

## 🧪 Test / Verifica

### 1. Test Configurazione

```powershell
# Verifica variabili d'ambiente
cat .env | Select-String "APP_CLIENT_SECRET|PORTAL_URL|APP_ID"

# Deve mostrare valori configurati (non vuoti)
```

### 2. Test Connettività Portal

```powershell
# Test autenticazione
curl -X POST http://localhost:3080/api/service-auth/token `
  -H "Content-Type:application/json" `
  -d '{"app_id":"pramaia-PramaIA-PrivacyManager","client_secret":"<secret>"}'

# Risposta attesa: {"access_token":"...","app_id":"..."}
```

### 3. Test Sync Licenza

```powershell
# Avvia app
uvicorn backend.main:app --reload

# Nel log deve apparire:
# ✅ License valid: LIC-PA-2026-0142 (expires: ..., sync: ok)
```

### 4. Test Admin UI

1. Login come admin
2. Vai a **Impostazioni → Licenza**
3. Verifica visualizzazione:
   - ✅ ID Licenza
   - ✅ Scadenza
   - ✅ Moduli
   - ✅ Checksum
   - ✅ Last Sync
   - ✅ Sync Status

---

## ⚠️ Troubleshooting

### Errore: "Portal client not configured"

**Causa:** `APP_CLIENT_SECRET` mancante in `.env`

**Fix:** Genera secret nel Portal e aggiungi a `.env`

---

### Errore: "Failed to get service token"

**Causa:** 
- Secret non valido
- App non registrata nel Portal

**Fix:**
1. Verifica secret corretto
2. Controlla registrazione: `GET /api/apps/PramaIA-PrivacyManager`

---

### Errore: "Portal unreachable and no local cache"

**Causa:** Primo avvio + Portal offline

**Fix:** Assicurarsi che Portal sia raggiungibile al primo avvio

---

## ✅ Checklist Pre-Release

Prima di rilasciare in produzione:

- [ ] Configurato `APP_CLIENT_SECRET` in `.env`
- [ ] Licenza ufficiale caricata nel Portal
- [ ] Test sync riuscito (check log avvio)
- [ ] Verificato endpoint `/api/license/status` (admin)
- [ ] Testato fallback offline (simulare Portal down)
- [ ] Audit log `license_logs` popolato
- [ ] Documentato moduli licenziati per cliente
- [ ] Configurato sync periodico (se implementato)

---

## 📚 Riferimenti

- [SPECIFICA_VERIFICA_LICENZE_APP.md](../../SPECIFICA_VERIFICA_LICENZE_APP.md) - Specifica tecnica ufficiale
- [LICENSE_SYSTEM_README.md](LICENSE_SYSTEM_README.md) - Guida utente completa
- [Backend Service](backend/services/license_service.py) - Implementazione
- [Portal Client](backend/services/portal_license_client.py) - Client sync

---

**Data Aggiornamento:** 27 Marzo 2026  
**Versione Specifica:** rev. 27/03/2026  
**Conformità:** ✅ 100% (nuovo modello Portal centrale implementato)
