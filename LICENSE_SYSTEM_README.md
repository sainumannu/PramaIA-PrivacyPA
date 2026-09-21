# Sistema di Verifica Licenze - Integrazione Portal

## Panoramica

Questo sistema implementa la specifica tecnica di verifica licenze PramaIA con integrazione **Portal centrale**:
- ✅ Sync periodico licenza da PramaIA Portal
- ✅ Autenticazione service-to-service con app_id + client_secret
- ✅ Copia locale come fallback offline
- ✅ Validazione payload secondo specifica tecnica
- ✅ Cache in memoria per performance
- ✅ Audit log di tutte le verifiche
- ❌ NO fallback silenziosi

## Modello Operativo

### Flusso Licenza

```
1. Admin carica licenza nel Portal per app_id
2. Portal conserva licenza attiva per ogni app
3. App ottiene service token dal Portal (app_id + client_secret)
4. App legge periodicamente licenza dal Portal
5. App salva copia locale verificata
6. Se Portal non raggiungibile → usa copia locale come fallback
```

## Configurazione Minima

### Variabili d'Ambiente (`.env`)

```env
PORTAL_URL=https://portal.example.com
APP_ID=PramaIA-PrivacyManager
APP_CLIENT_SECRET=<genera-nel-portal>
PRAMAIA_JWT_SECRET=<condiviso-con-portal>
DEPLOYMENT_TYPE=on_prem
```

### Generazione Credenziali Nel Portal

1. Login Portal come Admin
2. Settings → Apps → Trova app
3. Generate Service Credentials
4. Copia `client_secret` (visibile solo una volta!)
5. Salva in `.env`

## Uso nel Codice

```python
# Proteggere endpoint
@router.get("/api/albo")
@require_license(required_module="albo")
async def endpoint():
    ...

# Sync manuale
license_svc = LicenseService(db)
result = await license_svc.verify_license(sync_from_portal=True)
```

## API

- `GET /api/license/status` - Status licenza + sync info
- `POST /api/license/sync` - Forza sync da Portal

## Fallback Offline (3 livelli)

### Ordine Fallback

1. **Portal** (primario) - Sync da `<PORTAL_URL>/api/licenses/apps/{app_id}/current`
2. **Cache locale** (secondario) - File `license_cache.json` (salvato dopo sync Portal riuscito)
3. **File locale** (terziario) - File `license.jwt` + `license_public_key.pem` (vecchio modello, emergency fallback)

### File Cache: `license_cache.json`

Salvato automaticamente dopo ogni sync Portal riuscito.

Regole:
- Licenza non scaduta
- Controlli ambiente validi
- Se condizioni falliscono → prova file locale

### File Locale: `license.jwt` (Emergency Fallback)

Usato SOLO quando Portal non raggiungibile E nessuna cache disponibile.

Opzionale - può essere fornito come backup per situazioni di emergenza (es. Portal offline, rete assente).

File richiesti:
- `license.jwt` - Token firmato RS256
- `license_public_key.pem` - Chiave pubblica (opzionale, se presente verifica firma)

**⚠️ ATTENZIONE**: Il file locale NON sincronizza automaticamente - gestire manualmente.

📖 **Documentazione completa**: vedi sopra per dettagli.
