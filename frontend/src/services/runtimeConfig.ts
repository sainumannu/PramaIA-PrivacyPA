/**
 * Configurazione letta a runtime dal backend (/api/config).
 *
 * L'URL del Portal non può essere fissato nella build React (REACT_APP_*):
 * lo scrive l'installer nel .env del backend, quindi va letto a runtime.
 * Il valore è anche messo in cache in localStorage per i redirect che avvengono
 * prima della risposta del backend (es. logout su 401). authGuard.ts continua
 * a usare REACT_APP_PORTAL_URL come fallback finché non viene aggiornato per
 * leggere questo valore: questo modulo serve a tenere pronto, a runtime, il
 * portal_url scritto dall'installer, senza toccare altro.
 */
import axios from 'axios';

const PORTAL_URL_KEY = 'pramaia_portal_url';
const FALLBACK_PORTAL_URL = process.env.REACT_APP_PORTAL_URL || 'http://localhost:3080';

let portalUrl: string | null = null;

export function setPortalUrl(url?: string | null): void {
  if (!url) return;
  portalUrl = url.replace(/\/+$/, '');
  try {
    localStorage.setItem(PORTAL_URL_KEY, portalUrl);
  } catch {
    // localStorage non disponibile: resta il valore in memoria
  }
}

export function getPortalUrl(): string {
  if (portalUrl) return portalUrl;
  try {
    const cached = localStorage.getItem(PORTAL_URL_KEY);
    if (cached) return cached;
  } catch {
    // ignora
  }
  return FALLBACK_PORTAL_URL;
}

let configLoaded = false;
let configLoadPromise: Promise<void> | null = null;

/** Carica /api/config una sola volta (portal_url): il Setup Wizard resta
 * raggiungibile esplicitamente su /setup, non più come schermata che
 * intercetta l'intera app (vedi App.tsx). */
export function loadConfig(): Promise<void> {
  if (configLoaded) return Promise.resolve();
  if (configLoadPromise) return configLoadPromise;

  configLoadPromise = axios
    .get('/api/config')
    .then((response) => {
      setPortalUrl(response.data.portal_url);
    })
    .catch((err) => {
      console.error('Errore caricamento configurazione:', err);
      // Backend non ancora pronto: resta il valore di fallback/cache.
    })
    .finally(() => {
      configLoaded = true;
    });

  return configLoadPromise;
}
