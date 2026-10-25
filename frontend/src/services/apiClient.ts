import { authGuard, redirectToPortal } from './authGuard';
import { logger } from '../utils/logger';

const BASE_URL = process.env.REACT_APP_BACKEND_URL || '';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authGuard.getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const method = options.method || 'GET';
  const startTime = Date.now();
  
  // Log request
  logger.apiRequest(method, path, options.body ? JSON.parse(options.body as string) : undefined);

  try {
    const response = await fetch(`${BASE_URL}${path}`, { ...options, headers });
    const duration = Date.now() - startTime;
    
    // Log response
    logger.apiResponse(method, path, response.status, duration);

    if (response.status === 401) {
      authGuard.clearSession();
      redirectToPortal('La sessione è scaduta o non è più valida. Verrai reindirizzato al Portal per effettuare nuovamente l\'accesso.');
      throw new ApiError(401, 'Session expired. Redirecting to Portal...');
    }

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      const errorMsg = err.detail || 'Request failed';
      logger.apiError(method, path, errorMsg);
      throw new ApiError(response.status, errorMsg);
    }

    if (response.status === 204) return undefined as T;
    return response.json();
    
  } catch (error) {
    // Log error if not already logged (network errors, etc.)
    if (!(error instanceof ApiError)) {
      logger.apiError(method, path, error);
    }
    throw error;
  }
}

// Concorrenza ottimistica (§54): le modifiche indicano la versione letta con If-Match.
export interface WriteOptions {
  version?: number;
}
const versionHeaders = (opts?: WriteOptions): Record<string, string> =>
  opts?.version != null ? { 'If-Match': `"${opts.version}"` } : {};

export const apiClient = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown, opts?: WriteOptions) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body), headers: versionHeaders(opts) }),
  put: <T>(path: string, body: unknown, opts?: WriteOptions) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body), headers: versionHeaders(opts) }),
  patch: <T>(path: string, body: unknown, opts?: WriteOptions) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body), headers: versionHeaders(opts) }),
  delete: <T = void>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: async <T>(path: string, form: FormData): Promise<T> => {
    const token = authGuard.getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${BASE_URL}${path}`, { method: 'POST', headers, body: form });
    if (response.status === 401) {
      authGuard.clearSession();
      redirectToPortal('La sessione è scaduta o non è più valida. Verrai reindirizzato al Portal per effettuare nuovamente l\'accesso.');
      throw new ApiError(401, 'Session expired. Redirecting to Portal...');
    }
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new ApiError(response.status, err.detail || 'Upload failed');
    }
    return response.json();
  },
  // Esportazione con corpo JSON (POST): scarica il file e restituisce quante righe il server ha escluso per riservatezza.
  downloadPost: async (path: string, body: unknown, filename: string): Promise<{ excluded: number }> => {
    const token = authGuard.getToken();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${BASE_URL}${path}`, { method: 'POST', headers, body: JSON.stringify(body) });
    if (response.status === 401) {
      authGuard.clearSession();
      redirectToPortal('La sessione è scaduta o non è più valida. Verrai reindirizzato al Portal per effettuare nuovamente l\'accesso.');
      throw new ApiError(401, 'Session expired. Redirecting to Portal...');
    }
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new ApiError(response.status, err.detail || 'Download failed');
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    return { excluded: Number(response.headers.get('X-Excluded-Rows') || 0) };
  },
  download: async (path: string, filename: string) => {
    const token = authGuard.getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${BASE_URL}${path}`, { headers });
    if (!response.ok) throw new ApiError(response.status, 'Download failed');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
};
