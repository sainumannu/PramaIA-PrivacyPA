# Header della UI

**Versione del documento:** 1.0 (ottobre 2026). Riferimento unico: questo documento + `frontend/src/shared/Layout.tsx`. Chi modifica l'header aggiorna il documento e incrementa la versione.

Descrizione di come è realizzato l'header di PramaIA HelpDesk: struttura, elementi, aspetto grafico e comportamento di ciascun elemento (hover, focus, click, selezione).

**Scopo del documento:** descrivere l'header di HelpDesk in modo che gli altri servizi PramaIA possano riprodurlo identico e uniformare la UI.

## 0. Riferimenti

Tutti i percorsi sono relativi alla radice del repository PramaIA-HelpDesk.

| Cosa | Percorso (dalla radice del repository) |
|------|-------------------|
| Componente header (sorgente di riferimento) | `frontend/src/shared/Layout.tsx` |
| Temi e palette | `frontend/src/shared/theme.ts` |
| Provider del tema (CssBaseline, salvataggio scelta) | `frontend/src/shared/ThemeProvider.tsx` |
| Selettore lingua | `frontend/src/components/LanguageSwitcher.tsx` |
| Lingue, cambio lingua e sync col Portal | `frontend/src/i18n/i18n.ts` |
| Contesto tenant (elenco e selezione) | `frontend/src/services/tenantContext.tsx` |
| Token, utente, logout | `frontend/src/services/authGuard.ts` |
| URL del Portal a runtime | `frontend/src/services/config.ts` |
| Client API (header `X-Tenant-ID`, gestione 401) | `frontend/src/services/apiClient.ts` |
| Logo dell'header (file immagine) | `frontend/public/app-icon.ico` |
| Favicon della scheda del browser | `frontend/public/favicon.ico` |
| Font (link Google Fonts) | `frontend/public/index.html` |
| Stile globale (font del body) | `frontend/src/index.css` |

**Usato da:** `frontend/src/pages/DashboardPage.tsx` (titolo "Prama HelpDesk") e `frontend/src/pages/SettingsPage.tsx` (con breadcrumb "Impostazioni").

**Dipendenze** (da `frontend/package.json`):

| Pacchetto | Versione |
|-----------|----------|
| `react`, `react-dom` | ^18.2.0 |
| `@mui/material` | ^5.15.0 |
| `@mui/icons-material` | ^5.15.0 |
| `@emotion/react`, `@emotion/styled` | ^11.11.0 |
| `react-router-dom` | ^6.21.0 |
| `i18next`, `react-i18next` | ^23.10.0, ^14.0.5 |

> Nota: nello stesso repository esistono anche `frontend/src/components/PramaLayout.tsx` (header quasi identico, con gradiente del titolo arancio→ciano e sfondo al 90% di opacità) e `frontend/src/components/HelpDeskLayout.tsx` (stile Tailwind). **Nessuno dei due è usato da nessuna pagina**: l'header realmente mostrato è solo quello di `frontend/src/shared/Layout.tsx`. Il riferimento da seguire è questo.

---

## 1. Struttura generale

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ [logo] Prama HelpDesk  (⌂)  / Pagina      ····  [Utente] [Admin] [🏢 Tenant ▾] (⚙) (🌐) [🎨 Tema ▾] (⏻) │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
   1         2          3        4                     5       6          7        8    9       10        11
```

Ordine degli elementi, da sinistra a destra (concordato con i colleghi degli altri servizi):

| # | Elemento | Condizione di visibilità |
|---|----------|--------------------------|
| 1 | Icona Home dell'applicazione (link `/`) | sempre |
| 2 | Icona Portal (`Apps`, link all'URL del Portal) | sempre |
| 3 | Logo + titolo dell'applicazione (non cliccabili) | sempre |
| 4 | Breadcrumb `/ Titolo pagina` | solo se la pagina passa `pageTitle` |
| — | Spazio elastico (`flex: 1`) | sempre |
| 5 | Chip utente | se c'è un utente; **nascosto sotto i 900 px** (breakpoint `md`) |
| 6 | Badge ruolo nel servizio: Admin / Operatore / Utente | se c'è un utente; **nascosto sotto i 900 px** |
| 7 | Selettore tenant (il cambio **ricarica la pagina**) | se ci sono tenant disponibili |
| 8 | Icona Impostazioni | solo se `GET /api/helpdesk/settings` ha successo |
| 9 | Selettore lingua | sempre |
| 10 | Selettore tema | sempre |
| 11 | Logout | sempre |

Tutti i testi (tooltip, ruoli, "Caricamento tenant...") passano da i18n, namespace `helpdesk`, chiavi `header.home`, `header.portal`, `header.settings`, `header.logout`, `header.loading_tenants`, `header.role_admin`, `header.role_operator`, `header.role_user` (it, en, de, es, fr). Home e Portal sono link veri (`<a href>`): apribili in nuova scheda e raggiungibili da tastiera.

### Contenitore

| Proprietà | Valore |
|-----------|--------|
| Componente | `AppBar` con `position="sticky"`, `elevation={0}` (nessuna ombra) |
| Posizione | resta fissato in alto durante lo scroll (z-index standard MUI per l'AppBar: 1100) |
| Sfondo | `rgba(26, 26, 46, 0.85)`: blu scurissimo al 85% di opacità |
| Effetto vetro | `backdropFilter: blur(12px)`: il contenuto che scorre sotto è sfocato |
| Bordo | solo in basso, `1px solid rgba(255,255,255,0.08)` |
| Spaziatura tra elementi | `Toolbar` con `gap: 1` (8 px), più margini destri specifici sui singoli elementi |
| Altezza | quella predefinita del `Toolbar` MUI (56 px su schermi stretti, 64 px da 600 px di larghezza in su) |
| Font | `Inter` (con fallback Roboto/Helvetica/Arial), caricato da Google Fonts (`https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap`, incluso in `frontend/public/index.html`) |

**Importante:** colori di sfondo e di testo dell'header sono scritti direttamente nel codice (valori `rgba`/esadecimali fissi), non derivano dal tema. Per questo l'header **resta scuro in tutti e tre i temi** (Light, Dark, Blue). Cambiano invece, con il tema, i menu a tendina e le finestre che si aprono dall'header (vedi §4.2).

---

## 1bis. Icone

Tutte le icone sono **Material Icons** del pacchetto `@mui/icons-material` (stile "filled", il predefinito), tranne il logo che è un file immagine. Sono importate una per una (per esempio `import HomeIcon from '@mui/icons-material/Home';`). `fontSize="small"` corrisponde a **20 px** in MUI.

| Elemento | Icona | Import | Dimensione | Colore |
|----------|-------|--------|------------|--------|
| Logo dell'app | file immagine `app-icon.ico` (`frontend/public/app-icon.ico`, servito come `/app-icon.ico`) | tag `<img>` | 28 × 28 px, `object-fit: contain` | colori propri del file |
| Home | `Home` (casetta) | `@mui/icons-material/Home` | `fontSize="small"` (20 px) | `#f59e42` |
| Selettore tenant | `Business` (edificio), a inizio campo | `@mui/icons-material/Business` | 18 px | `rgba(255,255,255,0.7)` |
| Portal | `Apps` (griglia 3×3) | `@mui/icons-material/Apps` | `fontSize="small"` (20 px) | `rgba(255,255,255,0.7)` |
| Impostazioni | `Settings` (ingranaggio) | `@mui/icons-material/Settings` | `fontSize="small"` (20 px) | `rgba(255,255,255,0.7)` |
| Selettore lingua | `Language` (globo) | `{ Language as LanguageIcon } from '@mui/icons-material'` | `fontSize="small"` (20 px) | `rgba(255,255,255,0.7)` |
| Voci del menu lingua | emoji bandiera (🇮🇹 🇬🇧 🇫🇷 🇩🇪 🇪🇸), non icone MUI | testo nel `ListItemIcon` | 1,5 rem | colori propri dell'emoji |
| Selettore tema | `Palette` (tavolozza), a inizio campo | `@mui/icons-material/Palette` | 18 px | `rgba(255,255,255,0.7)` |
| Logout | `Logout` (freccia verso l'uscita) | `@mui/icons-material/Logout` | `fontSize="small"` (20 px) | `error.light` (`#ef5350`) |
| Freccia dei due selettori (tenant, tema) | freccia a tendina predefinita di MUI (`ArrowDropDown`) | interna al `Select` | predefinita | `#fff` |

Note sulle icone:
- I pulsanti icona (Home, Impostazioni, Lingua) sono `IconButton size="small"`; il Logout è un `IconButton` a dimensione standard, quindi visivamente un po' più grande.
- Il **favicon** della scheda è lo stesso file del logo (`frontend/public/favicon.ico`).
- Le bandiere emoji su Windows possono apparire come lettere (IT, GB): è accettato.
- Nella stessa cartella c'è anche `frontend/public/logo.png` (logo esteso, circa 2 MB), **non usato nell'header**.

---

## 2. Palette usata nell'header

| Uso | Colore |
|-----|--------|
| Sfondo header | `rgba(26, 26, 46, 0.85)` |
| Bordo inferiore | `rgba(255,255,255,0.08)` |
| Arancio di marca (`APP_COLOR`) | `#f59e42` |
| Arancio chiaro (fine gradiente del titolo) | `#f7b267` |
| Testo principale | `#fff` |
| Icone secondarie | `rgba(255,255,255,0.7)` |
| Bordo dei campi (riposo) | `rgba(255,255,255,0.4)` |
| Bordo dei campi (hover) | `rgba(255,255,255,0.6)` |
| Bordo chip utente | `rgba(255,255,255,0.3)` |
| Separatore breadcrumb `/` | `rgba(255,255,255,0.4)` |
| Testo badge Admin | `#1a1a2e` su sfondo `#f59e42` |
| Logout | `error.light` del tema (in MUI v5 è `#ef5350`) |
| Sfondo hover del logout | `rgba(239,68,68,0.1)` |

---

## 3. Elementi

### 3.1 Home dell'applicazione
- `IconButton` come `<a href="/">`, `size="small"`, icona `Home` (20 px), colore `#f59e42`. Tooltip `header.home`. Hover/click: alone e ripple standard MUI. Ricarica completa su `/` (la prima tab della Dashboard è Statistiche).

### 3.2 Portal e logo/titolo
- **Portal:** `IconButton` come `<a href={URL Portal}>`, icona `Apps`, colore `rgba(255,255,255,0.7)`, margine destro 4 px, tooltip `header.portal`. L'URL arriva a runtime da `GET /api/config` (`PORTAL_URL`).
- **Logo + titolo:** immagine `/app-icon.ico` 28 × 28 px e testo `h6`, peso 800, `letter-spacing: -0.5px`, gradiente `#f59e42` → `#f7b267` (`background-clip: text`). **Non sono cliccabili** e non hanno hover.

### 3.3 Breadcrumb della pagina

- Mostrato solo se la pagina passa `pageTitle`. Nella Dashboard non c'è; nelle Impostazioni compare `/ Impostazioni`.
- Il separatore `/` è `rgba(255,255,255,0.4)`; il titolo è `Typography body1`, bianco, peso 500.
- **Non è cliccabile.**

### 3.4 Chip utente

- **Componente:** `Chip` `size="small"`, `variant="outlined"`, altezza 28 px, testo 0,8 rem, bianco, bordo `rgba(255,255,255,0.3)`, margine destro 8 px.
- **Testo:** `display_name` del token, in mancanza l'email.
- **Interazione:** nessuna (non cliccabile, nessun hover specifico).

### 3.5 Badge ruolo nel servizio
- `Chip` pieno, altezza 24 px, testo 0,7 rem, grassetto 700, sfondo `#f59e42`, testo `#1a1a2e`. Nascosto sotto i 900 px.
- Mostra il ruolo **nel servizio HelpDesk**: Admin, Operatore o Utente (chi non è nel team). Il ruolo è calcolato come nelle tab della Dashboard (`getUserRole` con i membri del team da `GET /api/helpdesk/team/members`); l'admin del Portal risulta Admin. Nessuna interazione.

### 3.6 Selettore tenant

- **Componente:** `FormControl size="small"` (larghezza minima 160 px) con `Select`, altezza 32 px, testo 0,85 rem, bianco. In testa al campo c'è l'icona `BusinessIcon` (18 px, `rgba(255,255,255,0.7)`); la freccia del menu è bianca.
- **Bordo:** `rgba(255,255,255,0.4)` a riposo, `0.6` in hover. Lo stato di **focus** non è personalizzato: è quello di MUI (bordo nel colore primario, arancio `#f59e42`).
- **Dati:** la lista arriva da `GET /api/tenants/me` (il backend fa da proxy al Portal). Se la chiamata fallisce, si usano gli id dei tenant contenuti nel token (nome = id).
- **Caricamento:** mentre i tenant vengono caricati il campo è **disabilitato** (aspetto "disabled" di MUI) e il menu contiene la voce inattiva in corsivo "Caricamento tenant...".
- **Selezione iniziale:** all'avvio viene scelto, in ordine, il tenant salvato in `localStorage`, altrimenti quello già attivo, altrimenti il primo della lista. Non esiste più la voce "Tutti i tenant": si lavora sempre in un tenant specifico.
- **Menu aperto:** i colori del menu (sfondo, testo, voce selezionata evidenziata con una tinta dell'arancio) seguono il **tema corrente**, non l'header.
- **Cambio di selezione:**
  1. il tenant viene salvato in `localStorage`;
  2. dopo 100 ms la **pagina viene ricaricata** (`window.location.reload()`), così tutti i dati vengono richiesti di nuovo per il nuovo tenant.
  Da quel momento ogni chiamata API invia l'intestazione `X-Tenant-ID` col tenant scelto.

### 3.7 Icona Impostazioni

- **Componente:** `IconButton size="small"` con `SettingsIcon` (`fontSize="small"`), colore `rgba(255,255,255,0.7)`, margine destro 4 px.
- **Tooltip:** "Impostazioni" (testo fisso).
- **Visibilità:** all'avvio del layout viene fatta una chiamata `GET /api/helpdesk/settings`: se ha successo l'icona appare, se fallisce (per esempio utente senza permessi) resta nascosta. Di conseguenza l'icona **compare con un breve ritardo** e sposta di poco gli elementi a destra quando arriva la risposta.
- **Click:** `navigate('/settings')`, cambio di rotta interno senza ricarica.

### 3.8 Selettore lingua

- **Componente:** `LanguageSwitcher` (`frontend/src/components/LanguageSwitcher.tsx`): `IconButton size="small"` con icona `Language`, colore `rgba(255,255,255,0.7)`.
- **Tooltip:** il nome della lingua corrente (per esempio "Italiano").
- **Click:** apre un `Menu` ancorato sotto il pulsante con le 5 lingue supportate, ognuna con bandiera (emoji, 1,5 rem) e nome: 🇮🇹 Italiano, 🇬🇧 English, 🇫🇷 Français, 🇩🇪 Deutsch, 🇪🇸 Español. La voce della lingua in uso è marcata come **selezionata** (evidenziazione standard MUI). Su Windows le bandiere emoji sono spesso mostrate come coppie di lettere (IT, GB...) invece che come bandiere: è un limite dei font di sistema.
- **Scelta di una lingua:**
  1. `i18next` cambia lingua e **tutta l'interfaccia si traduce subito**, senza ricaricare;
  2. la scelta è salvata in `localStorage` (`helpdesk_language`);
  3. viene inviata al backend (`PUT /api/helpdesk/user/language`) per sincronizzarla con il Portal; se l'invio fallisce l'errore è silenzioso e la lingua locale resta applicata;
  4. il menu si chiude.
- Chiusura del menu anche con click fuori o tasto Esc (comportamento MUI).
- Lingua di default se non c'è nulla di salvato: lingua del browser, con ripiego su italiano.

### 3.9 Selettore tema

- **Componente:** `FormControl size="small"` (larghezza minima 100 px, margine destro 8 px) con `Select` identico nell'aspetto a quello del tenant (altezza 32 px, bianco, bordi 0,4 / 0,6 in hover), con icona `PaletteIcon` in testa.
- **Opzioni:** `Light`, `Dark`, `Blue`. Default: `Dark`.
- **Cambio di selezione:** applicato **immediatamente, senza ricaricare**; la scelta è salvata in `localStorage` (`pramaia_helpdesk_theme`) e ricordata alla visita successiva.
- **Cosa cambia con il tema** (palette in `frontend/src/shared/theme.ts`):

| | Light | Dark | Blue |
|---|-------|------|------|
| Modalità MUI | light | dark | light |
| Sfondo pagina | `#f4f6fb` | `#0f0f1a` | `#e0f2fe` |
| Sfondo superfici (card, menu) | `#e5e7ef` | `#1a1a2e` | `#bae6fd` |
| Testo principale | `#1a1a2e` | `#f1f5f9` | `#1e293b` |
| Testo secondario | `#f59e42` | `#94a3b8` | `#f59e42` |
| Colore primario | `#f59e42` | `#f59e42` | `#f59e42` |
| Colore secondario | `#06b6d4` | `#06b6d4` | `#38bdf8` |

- **Cosa NON cambia:** l'header stesso (colori fissi, vedi §1). Cambiano però lo sfondo della pagina, le card, il contenuto e i **menu a tendina aperti dall'header** (tenant, tema, lingua).
- Gli angoli arrotondati sono a 12 px in tutti i temi; i pulsanti hanno raggio 8 px e testo non maiuscolo.

### 3.10 Logout

- **Componente:** `IconButton` a dimensione standard (più grande degli altri, che sono `small`), icona `Logout` (`fontSize="small"`), colore `error.light` (rosso chiaro).
- **Hover:** sfondo `rgba(239,68,68,0.1)` (alone rosso tenue).
- **Tooltip:** "Logout" (testo fisso).
- **Click:**
  1. `authGuard.logout()` (in `frontend/src/services/authGuard.ts`) elimina token e utente da `localStorage`;
  2. il browser va al **Portal** (`window.location.href = getPortalUrl()`), dove l'utente può rifare l'accesso. La successiva chiamata a `navigate('/login')` nel codice non ha effetto pratico, perché la navigazione verso il Portal la sostituisce.
- Tenant, tema e lingua scelti restano salvati nel browser.

---

## 4. Comportamenti comuni

### 4.1 Stati dei controlli

| Stato | Comportamento |
|-------|---------------|
| Riposo | colori fissi descritti sopra |
| Hover (icone) | alone circolare standard MUI; per il logout alone rosso personalizzato |
| Hover (Select) | bordo da `0.4` a `0.6` di bianco |
| Focus da tastiera (Select, pulsanti) | anello/bordo standard MUI in colore primario (arancio) |
| Click (pulsanti icona) | ripple standard MUI |
| Disabilitato (Select tenant) | stile "disabled" MUI durante il caricamento |
| Tooltip | compaiono in hover e al focus da tastiera su Home, Impostazioni, Lingua, Logout |

### 4.2 Menu e finestre aperti dall'header

I menu (tenant, tema, lingua) si aprono sopra la pagina in un livello a parte e usano i **colori del tema attivo**, non quelli dell'header. Quindi con tema Light o Blue i menu sono chiari mentre la barra resta scura.

### 4.3 Adattamento a schermi stretti

Nel codice non ci sono breakpoint né elementi nascosti sui dispositivi piccoli: tutti gli elementi restano sulla stessa riga. Su schermi molto stretti la barra può risultare affollata (soprattutto con chip utente, badge Admin e i due selettori).

### 4.4 Persistenza

| Dato | Dove | Chiave |
|------|------|--------|
| Tema | `localStorage` | `pramaia_helpdesk_theme` |
| Lingua | `localStorage` (+ sync col backend/Portal) | `helpdesk_language` |
| Tenant selezionato | `localStorage` | `pramaia_selected_tenant` |
| Token e utente | `localStorage` | `pramaia_token`, `pramaia_user` |

---

## 5. Decisioni concordate e punti aperti

Decisioni con i colleghi degli altri servizi: header non segue il tema; stesso ordine e stesse icone Material (28 × 28 px per il logo); ruolo nel servizio nel badge; cambio tenant con ricarica pagina; testi via i18n; Home e Portal come link veri; chip utente e badge nascosti su schermi stretti; gli altri moduli si allineano a questo header (nessun componente condiviso né API per ora; da rivalutare se i moduli diventano molti o divergono).

Punti aperti:
- L'icona Impostazioni compare in ritardo (dipende da una chiamata API) e sposta gli elementi vicini.
- L'header scuro con tema Light/Blue crea contrasto con la pagina (scelta voluta).

---

## 6. Come allineare un altro modulo

1. Apri `frontend/src/shared/Layout.tsx` di HelpDesk e usalo come riferimento per struttura, ordine e stili.
2. Dipendenze: React 18, `@mui/material` e `@mui/icons-material` ^5.15, `react-router-dom` ^6, `react-i18next` (versioni in §0).
3. Riproduci l'ordine di §1 con le icone di §1bis (stesse dimensioni e colori) e i colori fissi di §2: l'header **non segue il tema**.
4. Logo del modulo: `app-icon.ico`, 28 x 28 px, in `public/`; favicon uguale.
5. Home e Portal come link veri (`<a href>`); logo e titolo non cliccabili.
6. Badge ruolo = ruolo **nel tuo modulo** (Admin / Operatore / Utente); chip utente e badge nascosti sotto i 900 px.
7. Cambio tenant: salva la scelta e ricarica la pagina.
8. Testi via i18n con le chiavi `header.*` elencate in §1 (it, en, de, es, fr). Traduzioni di riferimento in `frontend/src/locales/<lingua>/helpdesk.json`.
9. Verifica visiva su tema Light, Dark e Blue e a larghezza < 900 px.
