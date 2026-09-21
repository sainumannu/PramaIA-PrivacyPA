# Integrazione con PramaIA Portal

Guida per sviluppatori su come questa applicazione si integra con il Portal centralizzato.

---

## 🔐 Autenticazione SSO

**Questa app NON gestisce l'autenticazione.** Il login avviene esclusivamente tramite PramaIA Portal.

### Flusso

```
1. Utente accede all'app
2. App verifica token JWT in localStorage
3. Token assente/scaduto → redirect al Portal
4. Login sul Portal (credenziali o Microsoft OAuth)
5. Portal genera JWT e redirige a: {app_url}?token={JWT}
6. App salva token e lo usa per le chiamate API
```

### Configurazione Richiesta

```env
# .env — DEVE corrispondere al valore nel Portal
PRAMAIA_JWT_SECRET=<stesso-valore-del-portal>
PORTAL_URL=http://localhost:3080
```

⚠️ **Mai generare un JWT proprio** — usare sempre quello emesso dal Portal.

---

## 🎫 Struttura JWT Token

Il token contiene tutte le informazioni necessarie per autorizzazione e multi-tenancy:

```json
{
  "sub": "user-uuid",
  "email": "mario.rossi@acme.com",
  "display_name": "Mario Rossi",
  "roles": ["user"],
  "apps": ["PramaIA-PrivacyManager", "pramaia-helpdesk"],
  "tenant_id": "acme",
  "tenants": ["acme", "globex"],
  "tenant_role": "admin",
  "app_admin_for": ["PramaIA-PrivacyManager"],
  "iss": "pramaia-portal",
  "iat": 1711012345,
  "exp": 1711040945
}
```

### Campi Principali

| Campo | Tipo | Descrizione |
|-------|------|-------------|
| `sub` | string | User ID univoco (UUID) |
| `email` | string | Email utente |
| `display_name` | string | Nome visualizzato |
| `roles` | string[] | Ruoli globali: `admin`, `user`, `viewer` |
| `apps` | string[] | App a cui l'utente ha accesso |
| `tenant_id` | string | Tenant attivo corrente |
| `tenants` | string[] | Tutti i tenant dell'utente |
| `tenant_role` | string | Ruolo nel tenant: `global_admin`, `admin`, `member` |
| `app_admin_for` | string[] | App dove l'utente è App Admin (nel tenant corrente) |

---

## 🏢 Multi-Tenancy

Gli utenti possono appartenere a **più tenant** con permessi diversi per ciascuno.

### Regole di Isolamento

- **Utenti normali**: vedono solo i dati del `tenant_id` corrente
- **Tenant Admin**: gestiscono utenti/gruppi del proprio tenant
- **Global Admin**: accesso a tutti i tenant

### Implementazione nel Backend

```python
from backend.auth.portal_jwt import get_current_user, TokenPayload

@router.get("/my-data")
async def get_my_data(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Filtra SEMPRE per tenant_id
    tenant_id = user.raw.get("tenant_id", "default")
    
    result = await db.execute(
        select(MyModel).where(MyModel.tenant_id == tenant_id)
    )
    return result.scalars().all()
```

### Switch Tenant

L'utente può cambiare tenant dal Portal (genera nuovo JWT). L'app riceve automaticamente il nuovo token al prossimo accesso.

---

## 👥 Gerarchia Permessi (3 Livelli)

```
┌─────────────────────────────────────────┐
│           GLOBAL ADMIN                  │
│  (roles contiene "admin")               │
│  • Accesso totale a tutto               │
│  • Gestisce tutti i tenant              │
└─────────────────────────────────────────┘
                    │
        ┌───────────────────────────┐
        │       TENANT ADMIN        │
        │  (tenant_role = "admin")  │
        │  • Gestisce il suo tenant │
        │  • Assegna app agli utenti│
        └───────────────────────────┘
                    │
        ┌───────────────────────────┐
        │        APP ADMIN          │
        │  (app_admin_for contiene  │
        │   questa app)             │
        │  • Gestisce accessi app   │
        │  • Nel suo tenant         │
        └───────────────────────────┘
```

### Controlli nel Backend

```python
from backend.auth.portal_jwt import get_current_user, require_admin, TokenPayload

# Solo utenti autenticati
@router.get("/")
async def list_items(user: TokenPayload = Depends(get_current_user)):
    pass

# Solo Global Admin
@router.post("/admin-action")
async def admin_action(user: TokenPayload = Depends(require_admin)):
    pass

# Controllo Tenant Admin
def require_tenant_admin(user: TokenPayload = Depends(get_current_user)):
    tenant_role = user.raw.get("tenant_role", "member")
    if tenant_role not in ("admin", "global_admin"):
        raise HTTPException(403, "Tenant admin required")
    return user

# Controllo App Admin
def require_app_admin(user: TokenPayload = Depends(get_current_user)):
    app_admin_for = user.raw.get("app_admin_for", [])
    if "PramaIA-PrivacyManager" not in app_admin_for and not user.is_admin:
        raise HTTPException(403, "App admin required")
    return user
```

---

## 📡 API Portal Utili

L'app può chiamare le API del Portal passando il token dell'utente.

### Endpoint Principali

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/api/users/me` | Dati utente corrente |
| GET | `/api/tenants/me/tenants` | Tenant dell'utente |
| GET | `/api/apps/` | Lista app registrate |
| GET | `/api/groups/` | Gruppi del tenant |

### Esempio Chiamata

```python
import httpx

async def get_user_groups(token: str) -> list:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PORTAL_URL}/api/groups/",
            headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        return response.json()
```

---

## 📝 Registrazione App sul Portal

Prima che gli utenti possano accedere, l'app deve essere registrata sul Portal.

### Opzione 1: Script Automatico

```bash
python register_to_portal.py --admin-token <TOKEN_ADMIN>
```

### Opzione 2: API Diretta

```bash
POST http://localhost:8080/api/apps
Authorization: Bearer <admin_token>
Content-Type: application/json

{
  "app_id": "PramaIA-PrivacyManager",
  "name": "PrivacyManager",
  "description": "Gestione Privacy e GDPR PramaIA",
  "icon": "🛡️",
  "color": "#10b981",
  "url": "http://localhost:3003",
  "backend_url": "http://localhost:8003",
  "enabled": true
}
```

### Opzione 3: UI Admin

Accedi al Portal come admin → `/admin/apps` → "Nuova App"

---

## ⚙️ Pagina Impostazioni

L'app include una pagina `/settings` accessibile solo agli utenti autorizzati:

- **Global Admin** (`is_admin = true`)
- **Tenant Admin** (`tenant_role = 'admin'`)
- **App Admin** (`app_admin_for` contiene questa app)

### Funzionalità Incluse

1. **Gestione Utenti** — Visualizza utenti autorizzati dal Portal e assegna ruoli app-specifici
2. **Configurazione** — Pannello placeholder per configurazioni custom dell'app

### Come Estendere

Per aggiungere tab personalizzati, modifica `frontend/src/pages/SettingsPage.tsx`:

```tsx
<Tab icon={<MyIcon />} iconPosition="start" label="Mia Config" />
// ...
<TabPanel value={activeTab} index={2}>
  <MioComponente />
</TabPanel>
```

### Ruoli App-Specifici

L'app definisce ruoli interni in `frontend/src/components/UsersPanel.tsx`:

```ts
const APP_ROLES = [
  { value: 'user', label: 'Utente', color: 'default' },
  { value: 'operator', label: 'Operatore', color: 'primary' },
  { value: 'admin', label: 'Admin', color: 'error' },
];
```

Modifica questa lista per adattarla alle esigenze dell'app.

---

## ⚠️ Errori Comuni

| Errore | Causa | Soluzione |
|--------|-------|-----------|
| `Token expired` | JWT scaduto | Redirect al Portal per nuovo login |
| `Token not issued by PramaIA Portal` | JWT non valido | Verificare che `PRAMAIA_JWT_SECRET` corrisponda |
| `403 Forbidden` | Utente non ha accesso all'app | Assegnare permesso nel Portal |
| `tenant_id mismatch` | Dati di altro tenant | Filtrare sempre per `tenant_id` dal JWT |

---

## 🔗 Riferimenti

- **Validazione JWT**: `backend/auth/portal_jwt.py`
- **Auth Guard Frontend**: `frontend/src/services/authGuard.ts`
- **API Client**: `frontend/src/services/apiClient.ts`
- **Pagina Settings**: `frontend/src/pages/SettingsPage.tsx`
- **Gestione Team**: `backend/routers/settings_router.py`
- **Portal API Docs**: `http://localhost:8080/docs` (quando il Portal è in esecuzione)
