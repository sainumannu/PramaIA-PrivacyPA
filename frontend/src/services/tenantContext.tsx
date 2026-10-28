/**
 * TenantContext - tenant attivo dell'utente (selettore dell'header).
 *
 * Il tenant attivo è quello del token (`tenant_id`): è l'unica fonte di verità,
 * e i ruoli del token (tenant_role, app_admin_for) valgono per quel tenant.
 * Cambiare tenant significa chiedere al Portal un nuovo token (via backend):
 * il Portal verifica l'appartenenza e riemette il token coi ruoli del nuovo tenant.
 *
 * L'elenco arriva dal Portal (GET /api/tenants/me); se la chiamata fallisce si
 * usano i tenant presenti nel token (nome = id).
 */
import React, { createContext, useContext, useEffect, useState } from 'react';
import { apiClient } from './apiClient';
import { authGuard } from './authGuard';

export interface Tenant {
  id: string;
  name: string;
}

interface TenantContextValue {
  /** Tenant attivo (dal token) */
  selectedTenant: string | null;
  /** Cambia tenant e ricarica la pagina. Lancia un errore se il cambio non riesce. */
  switchTenant: (tenantId: string) => Promise<void>;
  availableTenants: Tenant[];
  isLoadingTenants: boolean;
}

interface SwitchTenantResponse {
  access_token: string;
  tenant_id: string;
  tenant_name: string;
}

const TenantContext = createContext<TenantContextValue | undefined>(undefined);

function tenantsFromToken(): Tenant[] {
  const user = authGuard.getCurrentUser();
  const ids = new Set<string>(user?.tenants || []);
  if (user?.tenant_id) ids.add(user.tenant_id);
  return Array.from(ids).map((id) => ({ id, name: id }));
}

export const TenantProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const user = authGuard.getCurrentUser();
  const selectedTenant = user?.tenant_id || null;
  const [fetchedTenants, setFetchedTenants] = useState<Tenant[] | null>(null);
  const [isLoadingTenants, setIsLoadingTenants] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const tenants = await apiClient.get<Tenant[]>('/api/tenants/me');
        if (!cancelled) setFetchedTenants(tenants);
      } catch {
        // Portal non raggiungibile: si resta sui tenant del token
      } finally {
        if (!cancelled) setIsLoadingTenants(false);
      }
    };

    if (authGuard.isAuthenticated()) {
      load();
    } else {
      setIsLoadingTenants(false);
    }
    return () => {
      cancelled = true;
    };
  }, [user?.sub]);

  // L'elenco contiene sempre il tenant attivo, così il selettore ha un valore valido
  const baseTenants = fetchedTenants ?? tenantsFromToken();
  const availableTenants =
    selectedTenant && !baseTenants.some((t) => t.id === selectedTenant)
      ? [{ id: selectedTenant, name: selectedTenant }, ...baseTenants]
      : baseTenants;

  const switchTenant = async (tenantId: string) => {
    if (tenantId === selectedTenant) return;
    const response = await apiClient.post<SwitchTenantResponse>('/api/tenants/switch', {
      tenant_id: tenantId,
    });
    if (!authGuard.setToken(response.access_token)) {
      throw new Error('Invalid token received while switching tenant');
    }
    // Ricarica: tutti i dati vanno richiesti di nuovo col token del nuovo tenant
    window.location.reload();
  };

  return (
    <TenantContext.Provider
      value={{ selectedTenant, switchTenant, availableTenants, isLoadingTenants }}
    >
      {children}
    </TenantContext.Provider>
  );
};

export function useTenant(): TenantContextValue {
  const context = useContext(TenantContext);
  if (!context) {
    throw new Error('useTenant must be used within TenantProvider');
  }
  return context;
}
