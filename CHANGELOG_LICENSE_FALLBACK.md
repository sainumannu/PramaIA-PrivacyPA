# 🔄 CHANGELOG - Sistema Licenze - Fallback Locale

## Data: 2025

## 🎯 Obiettivo

Aggiungere supporto per **fallback a file licenza locale** (`license.jwt`) quando:
- Portal PramaIA non raggiungibile
- Nessuna cache locale disponibile (`license_cache.json`)

Questo permette continuità operativa in caso di emergenza network/Portal.

---

## ✅ Modifiche Implementate

### 1. Backend: `backend/services/license_service.py`

**Aggiunte**:
- Costanti `LICENSE_FILE_PATH` e `PUBLIC_KEY_PATH` ripristinate
- Nuovo metodo `_load_local_license_file()`:
  - Carica `license.jwt` dalla root app
  - Verifica firma RS256 se `license_public_key.pem` presente
  - Decodifica senza verifica firma se chiave pubblica assente (con warning)
  - Ritorna `None` se file non esiste o decodifica fallisce

**Modifiche**:
- `sync_from_portal()`:
  - Aggiunto fallback terziario a `_load_local_license_file()`
  - Wrapping payload locale in formato compatibile con cache
  - Nuovo sync_status: `"local_file"`
  
- `verify_license()`:
  - Gestione caso Portal non configurato (`self.portal_client is None`)
  - Caricamento diretto da file locale se Portal assente
  - Nuovo sync_status: `"no_portal"`

**Comportamento**:
```
1. Sync da Portal (primario)
   ↓ fallisce
2. Carica license_cache.json (secondario)
   ↓ file assente
3. Carica license.jwt + verifica firma (terziario)
   ↓ file assente o invalido
4. ERRORE: nessuna licenza disponibile
```

---

### 2. Documentazione

**Nuovi file**:
- `LICENSE_LOCAL_FALLBACK.md`:
  - Guida completa al fallback locale
  - Generazione chiavi RSA
  - Creazione token JWT firmato
  - Deployment e monitoraggio
  - FAQ e troubleshooting

- `generate_emergency_license.py`:
  - Script interattivo per generare licenza di emergenza
  - Supporto parametri configurabili (moduli, utenti, scadenza)
  - Generazione automatica coppia chiavi RSA
  - Output pronto per deploy

- `license_public_key.pem.example`:
  - Template chiave pubblica (placeholder PEM)

**Aggiornamenti file esistenti**:
- `LICENSE_SYSTEM_README.md`:
  - Sezione "Fallback Offline" espansa con 3 livelli
  - Descrizione file locale emergency fallback
  - Warning su gestione manuale

- `README.md`:
  - Sistema licenze aggiornato con ordine fallback 3 livelli
  - Link a documentazione fallback locale

- `.gitignore`:
  - Aggiunti `license.jwt` e `license_public_key.pem`
  - Prevenzione commit accidentale credenziali

---

## 🔍 Testing

### Scenario 1: Portal Offline + Cache Disponibile
```python
# Setup
os.environ["PORTAL_URL"] = "http://portal-unreachable"
Path("license_cache.json").write_text('{"license_id": "cached-lic", ...}')

# Test
result = await license_service.verify_license(sync_from_portal=True)

# Atteso
assert result.sync_status == "using_cache"
assert result.valid == True
```

### Scenario 2: Portal Offline + No Cache + File Locale
```python
# Setup
os.environ["PORTAL_URL"] = "http://portal-unreachable"
Path("license_cache.json").unlink(missing_ok=True)
Path("license.jwt").write_text("<valid-jwt-token>")
Path("license_public_key.pem").write_text("<public-key-pem>")

# Test
result = await license_service.verify_license(sync_from_portal=True)

# Atteso
assert result.sync_status == "local_file"
assert result.valid == True
```

### Scenario 3: No Portal Config + File Locale
```python
# Setup
del os.environ["PORTAL_URL"]
del os.environ["APP_ID"]
Path("license.jwt").write_text("<valid-jwt-token>")

# Test
result = await license_service.verify_license()

# Atteso
assert result.sync_status == "local_file"
assert result.valid == True
```

### Scenario 4: Tutto Assente → Errore
```python
# Setup
os.environ["PORTAL_URL"] = "http://portal-unreachable"
Path("license_cache.json").unlink(missing_ok=True)
Path("license.jwt").unlink(missing_ok=True)

# Test
result = await license_service.verify_license(sync_from_portal=True)

# Atteso
assert result.valid == False
assert result.code == "LICENSE_MISSING"
assert "no valid local license file" in result.message
```

---

## 📋 Checklist Integrazione

- [x] Metodo `_load_local_license_file()` implementato
- [x] Verifica firma RS256 con PyJWT
- [x] Fallback a decodifica senza verifica se chiave pubblica assente
- [x] Integrazione in `sync_from_portal()` come terziario
- [x] Gestione caso Portal non configurato
- [x] Sync status `"local_file"` e `"no_portal"` aggiunti
- [x] `.gitignore` aggiornato
- [x] Documentazione completa scritta
- [x] Script generazione licenza emergenza creato
- [x] File example chiave pubblica fornito
- [x] README aggiornato con riferimenti
- [x] Nessun errore di sintassi (validated)

---

## ⚠️ Note Operative

### Sicurezza
- **Chiave privata** (`license_private_key.pem`):
  - ❌ MAI committare in repo
  - ✅ Conservare in vault sicuro (e.g., Azure Key Vault, HashiCorp Vault)
  - ✅ Accesso limitato solo a personale autorizzato

- **Chiave pubblica** (`license_public_key.pem`):
  - ✅ Può essere distribuita con app (verifica firma)
  - ⚠️ Se mancante → firma NON verificata (solo fallback emergenza)

### Deployment
- File `license.jwt` NON sincronizzato automaticamente
- Aggiornamento manuale richiesto se scadenza si avvicina
- Monitorare `sync_status` in `/api/license/status`:
  - `"ok"` → Portal funzionante
  - `"using_cache"` → Portal offline, cache valida
  - `"local_file"` → **EMERGENZA**: ripristinare Portal!

### Performance
- Caricamento file locale solo se Portal e cache falliscono
- Nessun impatto su performance normale (Portal primario)
- Verifica firma RS256 è computazionalmente leggera

---

## 🔗 Riferimenti

- Documentazione completa: `LICENSE_LOCAL_FALLBACK.md`
- Script generazione: `generate_emergency_license.py`
- Sistema licenze: `LICENSE_SYSTEM_README.md`
- Specifiche tecniche: `../SPECIFICA_VERIFICA_LICENZE_APP.md`

---

## 🎉 Conclusione

Il sistema di verifica licenze ora supporta **resilienza a 3 livelli**:
1. Portal (primario, aggiornamento dinamico)
2. Cache locale (secondario, offline breve termine)
3. File locale (emergency, offline lungo termine)

Questo garantisce continuità operativa anche in scenari di rete degradata, mantenendo al contempo centralizzazione gestionale tramite Portal.
