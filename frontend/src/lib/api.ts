/**
 * Minimal typed fetch wrapper for the IntelliCredit API.
 *
 * Phase-2 seam only: from Phase 3 the hand-written response interfaces below
 * are replaced by an openapi-typescript client generated from the FastAPI
 * OpenAPI document (ADR-0014 - contract drift becomes a compile error, not a
 * runtime surprise). Auth headers, refresh-token rotation, and the SSE
 * stream-ticket flow are added alongside the login screen in later phases.
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

/**
 * Error thrown for any non-2xx API response (and for unparsable bodies).
 * Carries the backend envelope fields when the body followed the contract.
 */
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

/** Base URL of the API; must be inlined at build time via NEXT_PUBLIC_*. */
export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

function isErrorEnvelope(body: unknown): body is ApiErrorEnvelope {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return false;
  }
  const err = (body as { error: unknown }).error;
  return typeof err === "object" && err !== null && "code" in err;
}

/**
 * GET `path` (e.g. "/api/v1/health") and parse the JSON body as `T`.
 * Non-2xx responses raise a typed {@link ApiError} built from the backend
 * error envelope when present, or a synthesized one otherwise.
 */
export async function apiGet<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      0,
      "network_error",
      `API unreachable at ${apiBaseUrl()}${path}`,
      { cause: String(cause) },
    );
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
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
