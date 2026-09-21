# 🌍 i18n Quick Reference — Developer Guide

## 🚀 Quick Start

### 1. Uso Base

```typescript
import { useTranslation } from '../i18n/useTranslation';

function MyComponent() {
  const { t } = useTranslation('common');
  
  return (
    <Button>{t('actions.save')}</Button>  // Output: "Salva" o "Save"
  );
}
```

### 2. Con Namespace Specifico

```typescript
const { t } = useTranslation('license');

<Typography>{t('status.valid')}</Typography>  // "Licenza valida"
```

### 3. Con Parametri

```typescript
// Traduzione: "Scade tra {{days}} giorni"
t('license:expires_in', { days: 30 })  // "Scade tra 30 giorni"
```

### 4. Pluralizzazione

```json
// locales/it/common.json
{
  "time.days": "{{count}} giorno",
  "time.days_plural": "{{count}} giorni"
}
```

```typescript
t('time.days', { count: 1 })   // "1 giorno"
t('time.days', { count: 5 })   // "5 giorni"
```

---

## 📁 Struttura File

```
frontend/src/
  i18n/
    i18n.ts              # Config principale
    types.ts             # TypeScript types
    useTranslation.ts    # Hook custom
  locales/
    it/
      common.json        # Traduzioni comuni
      license.json       # Namespace licenza
      settings.json      # Namespace impostazioni
      auth.json          # Namespace autenticazione
    en/
      (stessa struttura)
```

---

## 🎨 Namespace Disponibili

| Namespace | Uso | Esempi Chiavi |
|-----------|-----|---------------|
| `common` | Azioni, navigation, validazioni | `actions.save`, `status.loading` |
| `license` | Sistema licenze | `status.valid`, `sync_status.ok` |
| `settings` | Pagina impostazioni | `tabs.users`, `users.add_user` |
| `auth` | Autenticazione | `login.title`, `session.expired` |

---

## ✅ Checklist Aggiunta Funzionalità

Quando aggiungi una nuova feature con UI:

- [ ] Identifica testi da tradurre
- [ ] Scegli namespace appropriato (o crea nuovo)
- [ ] Aggiungi chiavi in `locales/it/[namespace].json`
- [ ] Aggiungi stesse chiavi (tradotte) in `locales/en/[namespace].json`
- [ ] Sostituisci testi hardcoded con `t('key')`
- [ ] Verifica: `npm run check:translations`
- [ ] Test cambio lingua dal browser

---

## 🛠️ Comandi Utili

```bash
# Verifica completezza traduzioni
cd frontend
npm run check:translations

# Avvia app con hot reload
npm start

# Build produzione
npm run build
```

---

## 🚫 Common Mistakes

### ❌ MALE: Testo hardcoded
```typescript
<Button>Salva</Button>
```

### ✅ BENE: Traduzione
```typescript
<Button>{t('common:actions.save')}</Button>
```

---

### ❌ MALE: Chiavi inconsistenti
```json
{
  "saveButton": "Salva",        // IT
  "save_button": "Save"         // EN (diversa naming)
}
```

### ✅ BENE: Chiavi identiche
```json
// IT
{ "actions.save": "Salva" }

// EN
{ "actions.save": "Save" }
```

---

### ❌ MALE: Interpolazione manuale
```typescript
`La licenza scade tra ${days} giorni`
```

### ✅ BENE: Interpolazione i18next
```typescript
t('license:expires_in', { days })
// Traduzione: "Scade tra {{days}} giorni"
```

---

## 🔄 Workflow Cambio Lingua

**Frontend:**
1. Utente clicca Language Switcher (header)
2. `changeLanguage(lang)` chiamato
3. Salvataggio in `localStorage`
4. UI si aggiorna automaticamente

**Integrazione Portal:**
- JWT include `preferred_language` → auto-rilevamento al login
- Cambio lingua sincronizzabile con Portal via API (futuro)

---

## 📦 Aggiungere Nuovo Namespace

**1. Crea file traduzioni:**
```bash
# Esempio: namespace "reports"
frontend/src/locales/it/reports.json
frontend/src/locales/en/reports.json
```

**2. Registra in config:**
```typescript
// frontend/src/i18n/i18n.ts
const resources = {
  it: {
    // ...esistenti
    reports: require('../locales/it/reports.json'),
  },
  en: {
    // ...esistenti
    reports: require('../locales/en/reports.json'),
  },
};

// Aggiungi a ns array
ns: ['common', 'license', 'settings', 'auth', 'reports'],
```

**3. Usa nel codice:**
```typescript
const { t } = useTranslation('reports');
```

---

## 🎯 Best Practices

1. **Un namespace per dominio**: Non mescolare traduzioni licenze con settings
2. **Chiavi descrittive**: `user_limit_exceeded`, non `error_123`
3. **Inglese per chiavi**: Anche se app primariamente italiana
4. **Verifica prima di commit**: Sempre run `check:translations`
5. **Nessun placeholder HTML**: Usa componenti React, non `<strong>` in JSON

---

## 🔍 Debug

### Vedere chiavi mancanti in console

```typescript
// i18n.ts
i18n.init({
  // ...
  debug: true,  // ← Aggiungi in dev
  saveMissing: true,
  missingKeyHandler: (lng, ns, key) => {
    console.warn(`Missing translation: ${ns}:${key} [${lng}]`);
  },
});
```

### Fallback lingua non funziona?

Verifica ordine detection:
```typescript
detection: {
  order: ['localStorage', 'navigator'],  // localStorage ha priorità
  caches: ['localStorage'],
}
```

---

## 📞 Help

**Chiavi mancanti segnalate da CI:**  
→ Run `npm run check:translations` locally e aggiungi chiavi

**Lingua non cambia:**  
→ Controlla che `app_language` sia in localStorage e reload

**Traduzioni Portal non sincronizzate:**  
→ Verifica JWT include campo `preferred_language`

---

## 🔗 Risorse

- **Documentazione completa team:** [I18N_PLATFORM_ALIGNMENT.md](../../I18N_PLATFORM_ALIGNMENT.md)
- **react-i18next docs:** https://react.i18next.com/
- **Interpolation guide:** https://www.i18next.com/translation-function/interpolation
