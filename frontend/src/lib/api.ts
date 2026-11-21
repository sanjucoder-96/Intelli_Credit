/**
 * Typed fetch client for the IntelliCredit API.
 *
 * The backend is the single source of truth (ARCHITECTURE invariant): this
 * client only transports JSON and attaches the bearer token. It never computes
 * a score, limit, rate, or decision — those arrive from backend contracts.
 * A 401 triggers one refresh-token rotation and a retry; a second failure
 * clears the session (the auth store redirects to /login).
 */

/** Shape of the backend error envelope (backend app/api/errors.py). */
export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown> | null;
    request_id: string | null;
  };
}

/** `GET /api/v1/health` response (backend HealthResponse schema). */
export interface HealthResponse {
  status: string;
  version: string;
  environment: string;
}

/** `GET /api/v1/ready` response (backend ReadyResponse schema). */
export interface ReadyResponse {
  status: string;
  checks: Record<string, boolean>;
}

/** Error thrown for any non-2xx API response (and for unparsable bodies). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;
  readonly requestId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> | null = null,
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

/** Base URL of the API; inlined at build time via NEXT_PUBLIC_*. */
export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

// --- token plumbing -------------------------------------------------------
// The auth store owns the tokens and registers callbacks here so this module
// stays free of React/zustand imports (and is usable from server-safe code).
let accessToken: string | null = null;
let onRefresh: (() => Promise<string | null>) | null = null;
let onAuthFailure: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}
export function registerRefreshHandler(fn: (() => Promise<string | null>) | null): void {
  onRefresh = fn;
}
export function registerAuthFailureHandler(fn: (() => void) | null): void {
  onAuthFailure = fn;
}

function isErrorEnvelope(body: unknown): body is ApiErrorEnvelope {
  if (typeof body !== "object" || body === null || !("error" in body)) return false;
  const err = (body as { error: unknown }).error;
  return typeof err === "object" && err !== null && "code" in err;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Skip auth header (login/refresh). */
  anonymous?: boolean;
  /** Internal: prevents infinite refresh recursion. */
  _isRetry?: boolean;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (!opts.anonymous && accessToken) headers.Authorization = `Bearer ${accessToken}`;

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
      signal: opts.signal,
    });
  } catch (cause) {
    throw new ApiError(0, "network_error", `API unreachable at ${apiBaseUrl()}${path}`, {
      cause: String(cause),
    });
  }

  // 401 → attempt one refresh, then retry once.
  if (response.status === 401 && !opts.anonymous && !opts._isRetry && onRefresh) {
    const refreshed = await onRefresh();
    if (refreshed) {
      return request<T>(path, { ...opts, _isRetry: true });
    }
    onAuthFailure?.();
  }

  let body: unknown = null;
  try {
    body = response.status === 204 ? null : await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    if (response.status === 401 && !opts.anonymous) onAuthFailure?.();
    if (isErrorEnvelope(body)) {
      throw new ApiError(
        response.status,
        body.error.code,
        body.error.message,
        body.error.details,
        body.error.request_id,
      );
    }
    throw new ApiError(
      response.status,
      "http_error",
      `Request failed with status ${response.status}`,
      body === null ? null : { body },
    );
  }

  return body as T;
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { signal });
}

/** Fetch a binary response (e.g. a rendered PDF) with the bearer token. */
export async function apiBlob(path: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(`${apiBaseUrl()}${path}`, { headers, cache: "no-store" });
  if (!response.ok) {
    throw new ApiError(response.status, "http_error", `Download failed (${response.status})`);
  }
  return response.blob();
}

/** Multipart upload (FormData); the browser sets the multipart boundary. */
export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method: "POST",
      headers,
      body: formData,
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(0, "network_error", `API unreachable at ${apiBaseUrl()}${path}`, {
      cause: String(cause),
    });
  }
  let body: unknown = null;
  try {
    body = response.status === 204 ? null : await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    if (response.status === 401) onAuthFailure?.();
    if (isErrorEnvelope(body)) {
      throw new ApiError(
        response.status,
        body.error.code,
        body.error.message,
        body.error.details,
        body.error.request_id,
      );
    }
    throw new ApiError(response.status, "http_error", `Upload failed (${response.status})`);
  }
  return body as T;
}
export function apiPost<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T> {
  return request<T>(path, { ...opts, method: "POST", body });
}
export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "PATCH", body });
}
export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}
