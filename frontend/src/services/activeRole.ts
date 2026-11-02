import { authGuard } from './authGuard';

// Ruolo con cui l'utente sceglie di agire (selettore della sidebar). Si invia come X-Active-Role e il server
// limita permessi e scope a quel ruolo; senza scelta valgono tutti i ruoli (e la scorciatoia degli amministratori).
// Si ricorda per utente ed ente nel browser: è una preferenza, non un dato da conservare.

const storageKey = (): string | null => {
  const user = authGuard.getCurrentUser();
  return user ? `privacypa.activeRole:${user.tenant_id || ''}:${user.sub || user.email}` : null;
};

export function getActiveRole(): string | null {
  try {
    const key = storageKey();
    return key ? localStorage.getItem(key) : null;
  } catch {
    return null;
  }
}

/** Cambia il ruolo attivo e ricarica la pagina: menu, permessi e dati si rileggono con il nuovo ruolo. */
export function setActiveRole(role: string | null, reload = true): void {
  try {
    const key = storageKey();
    if (key) {
      if (role) localStorage.setItem(key, role);
      else localStorage.removeItem(key);
    }
  } catch {
    // senza storage la scelta non si ricorda: resta il comportamento predefinito
  }
  if (reload) window.location.reload();
}

export function withActiveRole(headers: Record<string, string>): Record<string, string> {
  const role = getActiveRole();
  if (role) headers['X-Active-Role'] = role;
  return headers;
}
