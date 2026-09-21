# PrivacyManager

🛡️ Gestione Privacy e GDPR PramaIA

---

## 🚀 Quick Start

### 1. Prima configurazione (Setup Wizard)

**Metodo Consigliato: Setup Wizard Automatico**

Al primo avvio, l'applicativo mostra un **wizard guidato** che configura automaticamente:
- ✅ JWT Secret sincronizzato con Portal
- ✅ Credenziali service-to-service
- ✅ URL Portal e database

**Avvia l'app e segui il wizard:**

```powershell
# Installa dipendenze
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd frontend
npm install
cd ..

# Avvia app (wizard si aprirà automaticamente)
.\start-all.ps1
```

Il wizard ti guiderà attraverso 3 semplici step per configurare tutto automaticamente.

---

**Metodo Alternativo: Configurazione Manuale**

Se preferisci configurare manualmente (es. ambiente offline):

```powershell
Copy-Item .env.template .env
# Modifica .env e compila i campi richiesti
```

Poi esegui lo stesso comando di avvio.

---

### 2. Registrazione sul Portal

Dopo il setup, registra l'applicativo sul Portal:

```powershell
python register_to_portal.py
```

Lo script ti guiderà nel processo di registrazione automatica.

**IMPORTANTE:** Se hai usato il Setup Wizard in modalità "Fetch Automatico", dovrai prima registrare l'app nel Portal e ottenere l'App Secret da inserire nel wizard.

---

### 3. Primo accesso

Apri il browser su `http://localhost:3003` e:
- Se non configurato → appare Setup Wizard
- Se configurato ma non autenticato → redirect al Portal per login
- Se autenticato → app funzionante!

---

## 📁 Struttura progetto

```
PramaIA-PrivacyManager/
├── backend/
│   ├── routers/         # Endpoint FastAPI
│   ├── services/        # Logica di business
│   ├── models/          # Modelli SQLAlchemy
│   ├── db/              # Database config e init
│   └── auth/            # JWT validation (Portal SSO)
├── frontend/
│   ├── src/
│   │   ├── pages/       # Pagine React
│   │   ├── components/  # Componenti riutilizzabili
│   │   └── services/    # API client e authGuard
│   └── public/
├── mind_integration/    # Client PramaIA-Mind (opzionale)
└── register_to_portal.py
```

---

## 🔐 Autenticazione

L'autenticazione è gestita centralmente dal **PramaIA-Portal** tramite JWT.

- Il token JWT è emesso e validato dal Portal
- L'utente viene reindirizzato al Portal per il login
- Nessun form di login locale — l'auth è sempre SSO

**Mai modificare** `backend/auth/portal_jwt.py` senza coordinamento con il team Platform.

📖 **Per dettagli completi su SSO, multi-tenancy e permessi, vedi [PORTAL_INTEGRATION.md](PORTAL_INTEGRATION.md)**

---

## 🔒 Sistema Licenze

L'applicativo implementa un sistema completo di verifica licenze con integrazione **Portal centrale**:

- ✅ Sync automatico licenza da PramaIA Portal  
- ✅ Autenticazione service-to-service (app_id + client_secret)
- ✅ Fallback a 3 livelli: Portal → Cache locale → File locale
- ✅ Audit log completo
- ❌ Zero fallback silenziosi

**Ordine fallback**:
1. **Portal** (primario): Sync da `<PORTAL_URL>/api/licenses/apps/{app_id}/current`
2. **Cache** (secondario): File `license_cache.json` (salvato dopo sync riuscito)
3. **File locale** (emergency): File `license.jwt` + chiave pubblica (opzionale)

### Configurazione

Nel file `.env` configurare:

```env
PORTAL_URL=https://portal.example.com
APP_ID=PramaIA-PrivacyManager
APP_CLIENT_SECRET=<genera-nel-portal>
```

**Generare credenziali:**
1. Login Portal come Admin
2. Settings → Apps → Generate Service Credentials
3. Copia `client_secret` e salva in `.env`

### Uso

**Proteggere endpoint:**
```python
@router.get("/api/albo")
@require_license(required_module="albo")
async def get_albo():
    ...
```

**Monitorare stato (Admin UI):**
Vai a **Impostazioni → Licenza** per vedere:
- Validità e scadenze
- Moduli abilitati
- Status sync con Portal
- Checksum payload

📖 **Documentazione completa**: 
- [LICENSE_SYSTEM_README.md](LICENSE_SYSTEM_README.md) - Guida sistema licenze
- [LICENSE_LOCAL_FALLBACK.md](LICENSE_LOCAL_FALLBACK.md) - Fallback emergenza file locale

---

## 🔍 Sistema di Logging

L'applicativo include un **sistema di logging completo** per backend e frontend:

**Backend:**
- Log strutturati con rotazione automatica (max 10MB, 5 backup)
- Middleware per logging automatico richieste HTTP
- Configurazione livello via `LOG_LEVEL` env var
- Sanitizzazione dati sensibili

**Frontend:**
- Logger con colori in console  
- Log automatico chiamate API
- Error Boundary per catturare errori React
- Disabilitazione automatica in produzione (debug/info)

**Utilizzo rapido:**

```python
# Backend
from backend.utils.logging import get_logger
logger = get_logger(__name__)
logger.info("Operazione completata")
logger.error("Errore", exc_info=True)
```

```typescript
// Frontend
import { logger } from './utils/logger';
logger.info('Component mounted');
logger.error('Failed to load', error);
```

📖 **Guida completa**: [LOGGING_GUIDE.md](LOGGING_GUIDE.md)

---

## 🌍 Internazionalizzazione (i18n)

L'applicativo supporta **multi-lingua** con react-i18next:

**Lingue supportate:**
- 🇮🇹 Italiano (default)
- 🇬🇧 English

**Funzionalità:**
- ✅ Preferenza lingua da JWT Portal (campo `preferred_language`)
- ✅ Override locale con Language Switcher (header)
- ✅ Salvataggio preferenza in localStorage
- ✅ Traduzioni organizzate per namespace (common, license, settings, auth)

### Uso nel Codice

```typescript
import { useTranslation } from '../i18n/useTranslation';

const MyComponent = () => {
  const { t } = useTranslation('common');
  
  return <Button>{t('actions.save')}</Button>;  // "Salva" o "Save"
};
```

### Aggiungere Nuove Traduzioni

1. Modifica file in `frontend/src/locales/[lang]/[namespace].json`
2. Aggiungi chiave per tutte le lingue supportate
3. Verifica completezza con:
   ```bash
   cd frontend
   npm run check:translations
   ```

### Language Switcher

Disponibile nell'header di tutte le pagine. Cambio immediato senza reload.

📖 **Documentazione allineamento team**: [../I18N_PLATFORM_ALIGNMENT.md](../I18N_PLATFORM_ALIGNMENT.md)

---

## � Sistema Permessi

L'applicativo implementa un **sistema di permessi a 3 livelli** per gestire autorizzazioni complesse:

### Gerarchia Permessi

1. **Portal JWT Roles** (dal Portal)
   - **Global Admin**: Accesso totale a tutti i tenant e app
   - **Tenant Admin**: Gestione utenti e impostazioni del proprio tenant
   - **App Admin**: Gestione permessi app-specifici

2. **App-Level Roles** (locali all'app)
   - Ruoli personalizzati per l'applicativo (es. "Operator", "Viewer", "Manager")
   - Ciascun ruolo ha permessi granulari: `can_create`, `can_read`, `can_update`, `can_delete`, `can_assign`, `can_configure`

3. **Granular Permissions** (per risorsa)
   - Permessi specifici su singole risorse (es. "può leggere categoria 5")
   - Supporta allow/deny, scadenza permessi

### Proteggere Endpoint

```python
from backend.auth.permissions import require_app_admin, require_permission

# Solo App Admin
@router.post("/api/roles")
def create_role(user: dict = Depends(require_app_admin())):
    ...

# Permesso specifico
@router.delete("/api/items/{item_id}")
def delete_item(user: dict = Depends(require_permission("can_delete", "item"))):
    ...
```

### Verifiche nel Codice

```python
from backend.auth.permissions import is_global_admin, user_can

# Verifica ruolo Portal
if is_global_admin(user_data):
    # Accesso totale
    pass

# Verifica permesso granulare
if user_can(user_id, tenant_id, "can_delete", db, "category", "5"):
    # User può eliminare categoria 5
    pass
```

### Admin UI

La gestione permessi è disponibile in **Impostazioni → Permessi** (solo per Admin):
- **Tab Ruoli**: Crea, modifica, elimina ruoli app-specifici
- **Tab Ruoli Utente**: Assegna/revoca ruoli agli utenti
- **Tab Audit Logs**: Log completo delle modifiche ai permessi

### Database

Il sistema crea automaticamente 4 tabelle:
- `app_roles`: Ruoli personalizzati app
- `user_app_roles`: Assegnazioni ruolo → utente
- `app_permissions`: Permessi granulari su risorse
- `app_audit_logs`: Audit trail modifiche permessi

### Isolamento Tenant

- Tutti i permessi sono filtrati per `tenant_id`
- Un utente può accedere **solo** a risorse del proprio tenant
- Global Admin può accedere a **tutti** i tenant

📖 **Documentazione completa**: [PERMISSIONS_SYSTEM.md](PERMISSIONS_SYSTEM.md)

---

## �🛠️ Sviluppo

### Aggiungere un nuovo endpoint

1. Crea un router in `backend/routers/`
2. Implementa la logica in `backend/services/`
3. Se serve persistenza, aggiungi un modello in `backend/models/`
4. Registra il router in `backend/main.py`

### Aggiungere una nuova pagina

1. Crea il componente in `frontend/src/pages/`
2. Aggiungi la route in `frontend/src/App.tsx`
3. Chiama le API tramite `services/apiClient.ts`

---

## 🧪 Testing

**Backend**:
```powershell
pytest
```

**Frontend**:
```powershell
cd frontend
npm test
```

---

## 🐳 Docker

Avvia l'intero stack con Docker Compose:

```powershell
docker-compose up --build
```

- Backend: http://localhost:8003
- Frontend: http://localhost:3003

---

## 📚 Risorse

- [PramaIA-Portal](https://github.com/sainumannu/PramaIA-Portal) - Autenticazione e gestione app
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [React Docs](https://react.dev/)

---

## 📝 Note

- **Database**: SQLite (`PramaIA-PrivacyManager.db`), le tabelle vengono create automaticamente all'avvio
- **CORS**: Configurato per accettare richieste dal Portal e dal frontend locale
- **Porte**: Backend 8003, Frontend 3003
---

## 🐞 Troubleshooting

### Errore "PRAMAIA_JWT_SECRET not set"
→ Imposta la variabile in `.env` con lo stesso valore del Portal

### Token JWT non valido
→ Controlla che `PRAMAIA_JWT_SECRET` sia identico tra Portal e questa app

### Frontend non si connette al backend
→ Verifica che `REACT_APP_BACKEND_URL` in `docker-compose.yml` punti correttamente al backend

---

**Generato da**: PramaIA-AppTemplate  
**Autore**: PramaIA Team
