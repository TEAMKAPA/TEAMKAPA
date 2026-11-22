import type { ApiErrorBody, ApiErrorResponse } from '../types/auditReport';

const TOKEN_STORAGE_KEY = 'teamkapa_jwt_token';

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiClientError';
    this.status = status;
    this.code = body.code;
    this.details = body.details;
  }
}

export function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL as string | undefined;
  return configured ? configured.replace(/\/+$/, '') : '';
}

export function getStoredToken(): string {
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setStoredToken(token: string): void {
  try {
    const trimmed = token.trim();
    if (trimmed) {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, trimmed);
    } else {
      window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    // Brak dostępu do sessionStorage
  }
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (typeof value !== 'object' || value === null || !('error' in value)) {
    return false;
  }
  const err = (value as { error: unknown }).error;
  return (
    typeof err === 'object' &&
    err !== null &&
    'code' in err &&
    'message' in err &&
    typeof (err as { code: unknown }).code === 'string' &&
    typeof (err as { message: unknown }).message === 'string'
  );
}

export interface ApiRequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: BodyInit | null;
  headers?: Record<string, string>;
  token?: string;
  signal?: AbortSignal;
}

export async function apiRequestRaw(
  path: string,
  options: ApiRequestOptions = {},
): Promise<Response> {
  const url = `${getApiBaseUrl()}${path}`;
  const headers = new Headers(options.headers);
  const token = options.token !== undefined ? options.token : getStoredToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? 'GET',
      headers,
      body: options.body ?? undefined,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err;
    }
    throw new ApiClientError(0, {
      code: 'NETWORK_ERROR',
      message: 'Brak połączenia z serwerem API. Sprawdź backend lub użyj trybu demonstracyjnego.',
    });
  }

  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      // Odpowiedź nie jest JSON-em
    }

    if (isApiErrorResponse(parsed)) {
      throw new ApiClientError(response.status, parsed.error);
    }

    throw new ApiClientError(response.status, {
      code: `HTTP_${response.status}`,
      message: `Błąd serwera HTTP ${response.status} (${response.statusText || 'nieznany błąd'})`,
    });
  }

  return response;
}

export async function apiJson<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const response = await apiRequestRaw(path, options);
  return (await response.json()) as T;
}
