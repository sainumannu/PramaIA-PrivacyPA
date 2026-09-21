# Sistema di Logging - PrivacyManager

Ogni app generata dal template include un sistema di logging completo e configurabile per backend e frontend.

---

## 🎯 Funzionalità

### Backend (Python/FastAPI)
- ✅ Log strutturati con timestamp, livello, modulo, funzione
- ✅ Rotazione automatica file (max 10MB, 5 backup)
- ✅ Output colorato in console
- ✅ Middleware per logging automatico richieste HTTP
- ✅ Configurazione livello via `LOG_LEVEL` env var
- ✅ Sanitizzazione dati sensibili (token, password)

### Frontend (React/TypeScript)
- ✅ Logger con colori e icone in console
- ✅ Log automatico chiamate API (request/response/errors)
- ✅ Error Boundary per catturare errori React
- ✅ Disabilitazione automatica in produzione (debug/info)
- ✅ Stub per integrazione servizi monitoring (Sentry, LogRocket)

---

## 📚 Utilizzo Backend

### Import e configurazione

Il logger è già configurato in `backend/main.py`:

```python
from backend.utils.logging import get_logger

logger = get_logger(__name__)
```

### Logging nei router/services

```python
from backend.utils.logging import get_logger

logger = get_logger(__name__)

def my_function():
    logger.debug("Dettagli debug (solo development)")
    logger.info("Operazione completata con successo")
    logger.warning("Attenzione: valore inatteso")
    logger.error("Errore durante elaborazione", exc_info=True)
    
    # Con dati strutturati
    logger.info(f"User {user_id} logged in", extra={"user_id": user_id, "ip": client_ip})
```

### Livelli di log

| Livello | Quando usare |
|---------|-------------|
| `DEBUG` | Informazioni dettagliate per debugging (query SQL, payload) |
| `INFO` | Eventi normali applicazione (user login, operazioni completate) |
| `WARNING` | Eventi anomali non bloccanti (deprecation, valori inattesi) |
| `ERROR` | Errori che impediscono un'operazione specifica |
| `CRITICAL` | Errori fatali che impediscono l'avvio dell'app |

### Middleware HTTP Logging

Il middleware `LoggingMiddleware` logga automaticamente tutte le richieste HTTP:

```
2026-04-07 10:30:15 | INFO     | middleware | → GET /api/users?page=1 | client=127.0.0.1
2026-04-07 10:30:15 | INFO     | middleware | ← GET /api/users | 200 | 45ms
```

**Endpoint esclusi** (troppo verbosi):
- `/api/health`, `/health`
- `/docs`, `/openapi.json`, `/redoc`

### Configurazione

Nel file `.env`:

```env
# LOG_LEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL=INFO
```

**Raccomandazioni:**
- **Development**: `DEBUG` o `INFO`
- **Staging**: `INFO`
- **Production**: `WARNING` o `ERROR`

### File di log

I log sono salvati in `logs/PramaIA-PrivacyManager.log`:
- **Max size**: 10MB per file
- **Rotazione**: Mantiene ultimi 5 file
- **Formato**: `app.log`, `app.log.1`, `app.log.2`, ..., `app.log.5`

---

## 📚 Utilizzo Frontend

### Import logger

```typescript
import { logger } from './utils/logger';
```

### Logging nel codice

```typescript
// Info generale
logger.info('Component mounted');

// Debug (solo in development)
logger.debug('User data loaded', userData);

// Warning
logger.warn('Deprecated feature used');

// Error
logger.error('Failed to load data', error);
```

### Logging API (automatico)

Il logger è già integrato in `apiClient.ts` e logga automaticamente:

```
[PrivacyManager] 🔍 → API GET /api/users
[PrivacyManager] 🔍 ← API GET /api/users | 200 (145ms)
```

In caso di errore:
```
[PrivacyManager] ❌ ✗ API POST /api/users failed
Error details: { status: 400, message: "Validation error" }
```

### Error Boundary

L'`ErrorBoundary` cattura automaticamente errori React non gestiti:

```tsx
// Già implementato in App.tsx
<ErrorBoundary>
  <YourApp />
</ErrorBoundary>
```

Quando si verifica un errore:
1. Viene loggato con `logger.error()`
2. Viene mostrata una UI di errore user-friendly
3. In development, viene mostrato lo stack trace

### Disabilitare log in produzione

I log `debug` e `info` sono automaticamente disabilitati in produzione:

```typescript
// Solo in development
logger.debug('Detailed debug info');  // ❌ Non appare in production
logger.info('User action');           // ❌ Non appare in production

// Sempre visibili
logger.warn('Warning message');       // ✅ Appare anche in production
logger.error('Error occurred');       // ✅ Appare anche in production
```

---

## 🔒 Sicurezza

### Dati sensibili

**❌ MAI loggare:**
- Password
- Token JWT
- API keys
- Numeri carte di credito
- Dati personali sensibili

**✅ Sanitizzazione automatica:**

Il backend sanitizza automaticamente header sensibili:

```python
# Header originali
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJ...
X-Api-Key: sk_live_abc123...

# Nel log
Authorization: ***REDACTED***
X-Api-Key: ***REDACTED***
```

Headers sanitizzati:
- `authorization`
- `cookie`, `set-cookie`
- `x-api-key`, `x-app-secret`
- `x-auth-token`

### Best practices

```python
# ❌ ERRATO - logga password
logger.info(f"User logged in: {username} / {password}")

# ✅ CORRETTO
logger.info(f"User logged in: {username}")

# ❌ ERRATO - logga token completo
logger.debug(f"JWT token: {token}")

# ✅ CORRETTO - logga solo parte o hash
logger.debug(f"JWT token (first 10 chars): {token[:10]}...")
```

---

## 🔍 Troubleshooting

### I log non appaiono in console

1. Verifica `LOG_LEVEL` in `.env`
2. Assicurati che il livello del messaggio sia >= LOG_LEVEL
3. Riavvia l'app dopo aver modificato `.env`

### File log troppo grandi

Il sistema di rotazione automatica è già configurato (max 10MB, 5 file).

Se necessario, puoi personalizzare in `backend/utils/logging.py`:

```python
setup_logging(
    max_bytes=5 * 1024 * 1024,  # 5MB invece di 10MB
    backup_count=3               # 3 file invece di 5
)
```

### Log frontend non appaiono

- I log `debug` e `info` sono disabilitati in production
- Controlla console browser (F12)
- Verifica che l'import del logger sia corretto

---

## 🚀 Integrazione Monitoring Services

### Backend (Sentry example)

```python
# backend/utils/logging.py - sendToMonitoring()

import sentry_sdk

def setup_logging():
    # ... existing code ...
    
    # Inizializza Sentry
    if os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            environment=os.getenv("ENVIRONMENT", "development"),
        )
```

### Frontend (Sentry example)

```typescript
// frontend/src/utils/logger.ts - sendToMonitoring()

import * as Sentry from "@sentry/react";

private sendToMonitoring(entry: LogEntry): void {
  if (process.env.REACT_APP_SENTRY_DSN) {
    Sentry.captureMessage(entry.message, {
      level: entry.level as any,
      extra: entry.data,
    });
  }
}
```

---

## 📖 Esempi Completi

### Backend: Router con logging

```python
from fastapi import APIRouter, HTTPException
from backend.utils.logging import get_logger

router = APIRouter(prefix="/api/users", tags=["Users"])
logger = get_logger(__name__)

@router.get("/")
async def list_users():
    logger.info("Fetching user list")
    
    try:
        users = await get_users_from_db()
        logger.info(f"Found {len(users)} users")
        return users
        
    except Exception as e:
        logger.error("Failed to fetch users", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
```

### Frontend: Componente React con logging

```typescript
import React, { useEffect, useState } from 'react';
import { logger } from './utils/logger';
import { apiClient } from './services/apiClient';

export default function UserList() {
  const [users, setUsers] = useState([]);
  
  useEffect(() => {
    logger.debug('UserList component mounted');
    loadUsers();
  }, []);
  
  const loadUsers = async () => {
    try {
      // API call già loggata da apiClient
      const data = await apiClient.get('/api/users');
      setUsers(data);
      logger.info(`Loaded ${data.length} users`);
    } catch (error) {
      logger.error('Failed to load users', error);
      // Mostra errore all'utente
    }
  };
  
  return <div>{/* UI */}</div>;
}
```

---

## 🎓 Best Practices Summary

✅ **DO:**
- Usare il livello appropriato per ogni messaggio
- Includere context nei log (user_id, operation, etc.)
- Loggare inizio/fine operazioni lunghe
- Usare `exc_info=True` per includere stack trace negli errori
- Sanitizzare dati sensibili

❌ **DON'T:**
- Loggare password, token, API keys
- Usare `DEBUG` in production
- Loggare ogni singola riga di codice
- Loggare dentro loop (troppo verbose)
- Lasciare `print()` nel codice (usare `logger` invece)

---

Per domande o problemi con il logging, consulta la documentazione FastAPI/React o apri un issue nel repository del template.
