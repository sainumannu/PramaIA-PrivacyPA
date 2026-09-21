/**
 * authGuard — legge il token JWT emesso da PramaIA Portal.
 *
 * Il Portal invia il token come query param ?token=<JWT> al primo redirect,
 * oppure lo trova già in localStorage da una sessione precedente.
 */

import { setLanguageFromToken } from '../i18n/i18n';

const TOKEN_KEY = 'pramaia_token';
const USER_KEY = 'pramaia_user';

export interface PortalUser {
  sub: string;
  email: string;
  display_name: string;
  roles: string[];
  apps: string[];
  is_admin: boolean;
  tenant_id?: string;
  tenants?: string[];
  tenant_role?: string; // 'global_admin' | 'admin' | 'member'
  app_admin_for?: string[];
  preferred_language?: string; // Lingua preferita utente
}

function parseJwtPayload(token: string): PortalUser | null {
  try {
    const base64 = token.split('.')[1];
    const json = atob(base64.replace(/-/g, '+').replace(/_/g, '/'));
    const payload = JSON.parse(json);
    return {
      sub: payload.sub,
      email: payload.email,
      display_name: payload.display_name || payload.email,
      roles: payload.roles || [],
      apps: payload.apps || [],
      is_admin: (payload.roles || []).includes('admin'),
      tenant_id: payload.tenant_id,
      tenants: payload.tenants || [],
      tenant_role: payload.tenant_role,
      preferred_language: payload.preferred_language,
      app_admin_for: payload.app_admin_for || [],
    };
  } catch {
    return null;
  }
}

function isTokenExpired(token: string): boolean {
  try {
    const base64 = token.split('.')[1];
    const payload = JSON.parse(atob(base64.replace(/-/g, '+').replace(/_/g, '/')));
    return payload.exp * 1000 < Date.now();
  } catch {
    return true;
  }
}

export const authGuard = {
  /**
   * Chiama questo all'avvio dell'app per raccogliere il token
   * dal query param ?token= (redirect dal Portal) o dal localStorage.
   */
  init(): void {
    const params = new URLSearchParams(window.location.search);
    const tokenFromUrl = params.get('token');
    if (tokenFromUrl) {
      localStorage.setItem(TOKEN_KEY, tokenFromUrl);
      const user = parseJwtPayload(tokenFromUrl);
      if (user) {
        localStorage.setItem(USER_KEY, JSON.stringify(user));
        // Imposta lingua da preferenza Portal
        if (user.preferred_language) {
          setLanguageFromToken(user.preferred_language);
        }
      }
      // Rimuove il token dall'URL per sicurezza
      params.delete('token');
      const newUrl = window.location.pathname + (params.toString() ? `?${params}` : '');
      window.history.replaceState({}, '', newUrl);
    } else {
      // Token già in localStorage - verifica preferenza lingua
      const user = this.getCurrentUser();
      if (user?.preferred_language) {
        setLanguageFromToken(user.preferred_language);
      }
    }
  },

  getToken(): string | null {
    return localStorage.getItem(TOKEN_KEY);
  },

  getCurrentUser(): PortalUser | null {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch { return null; }
  },

  isAuthenticated(): boolean {
    const token = this.getToken();
    return !!token && !isTokenExpired(token);
  },

  logout(): void {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    const portalUrl = process.env.REACT_APP_PORTAL_URL || 'http://localhost:3080';
    window.location.href = portalUrl;
  },
};

// Inizializza subito all'import
authGuard.init();

/**
 * Verifica se l'utente corrente può accedere alle impostazioni dell'app.
 * Ritorna true se:
 * - È Global Admin (is_admin = true)
 * - È App Admin per questa app (app_admin_for contiene l'app_id)
 * - È Tenant Admin (tenant_role = 'admin')
 */
export function canAccessSettings(): boolean {
  const user = authGuard.getCurrentUser();
  if (!user) return false;
  
  // Global admin
  if (user.is_admin) return true;
  
  // Tenant admin
  if (user.tenant_role === 'admin' || user.tenant_role === 'global_admin') return true;
  
  // App admin per questa app
  const appId = process.env.REACT_APP_APP_ID || 'PramaIA-PrivacyManager';
  if (user.app_admin_for?.includes(appId)) return true;
  
  return false;
}

/**
 * Verifica se l'utente corrente è un Global Admin (Platform Admin).
 */
export function isGlobalAdmin(): boolean {
  const user = authGuard.getCurrentUser();
  if (!user) return false;
  return user.tenant_role === 'global_admin';
}

/**
 * Verifica se l'utente corrente è un Tenant Admin del proprio tenant.
 */
export function isTenantAdmin(): boolean {
  const user = authGuard.getCurrentUser();
  if (!user) return false;
  return user.tenant_role === 'admin' || user.tenant_role === 'global_admin';
}

/**
 * Verifica se l'utente corrente è un App Admin di questa app.
 */
export function isAppAdmin(): boolean {
  const user = authGuard.getCurrentUser();
  if (!user) return false;
  
  const appId = process.env.REACT_APP_APP_ID || 'PramaIA-PrivacyManager';
  return (
    user.tenant_role === 'global_admin' ||
    Boolean(user.app_admin_for && user.app_admin_for.includes(appId))
  );
}

/**
 * Ottiene i dati utente correnti (alias di authGuard.getCurrentUser()).
 */
export function getUserData(): PortalUser | null {
  return authGuard.getCurrentUser();
}
