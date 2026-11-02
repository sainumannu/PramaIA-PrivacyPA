import { useEffect, useState } from 'react';
import { apiClient } from './apiClient';
import { getActiveRole, setActiveRole } from './activeRole';

// Permessi effettivi dell'utente (§10.1): servono a nascondere menu e pulsanti non utilizzabili.
// L'enforcement resta sul server: se il caricamento fallisce non si nasconde nulla.
export interface PermissionInfo {
  allowed: boolean;
  tenant_wide: boolean;
  unit_ids: string[];
}

export interface MyPermissions {
  user_id: string;
  tenant_id: string;
  is_admin: boolean;
  is_portal_admin?: boolean;
  active_role?: string | null;
  available_roles?: string[];
  permissions: Record<string, PermissionInfo>;
}

let cache: MyPermissions | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

const load = (): Promise<void> => {
  if (!inflight) {
    inflight = apiClient
      .get<MyPermissions>('/api/privacypa/me/permissions')
      .then((p) => {
        // Ruolo ricordato ma non più assegnato (revocato, scaduto): si torna a tutti i ruoli.
        const stored = getActiveRole();
        if (stored && !(p.available_roles || []).includes(stored)) {
          setActiveRole(null);
          return;
        }
        cache = p;
      })
      .catch(() => {
        cache = null;
      })
      .finally(() => {
        inflight = null;
        listeners.forEach((l) => l());
      });
  }
  return inflight;
};

export const refreshPermissions = () => load();

export function usePermissions() {
  const [, setTick] = useState(0);

  useEffect(() => {
    const listener = () => setTick((n) => n + 1);
    listeners.add(listener);
    if (!cache) load();
    return () => {
      listeners.delete(listener);
    };
  }, []);

  return {
    loaded: cache !== null,
    activeRole: cache?.active_role || null,
    availableRoles: cache?.available_roles || [],
    isPortalAdmin: Boolean(cache?.is_portal_admin),
    can: (code: string): boolean => (cache ? Boolean(cache.permissions[code]?.allowed) : true),
  };
}
