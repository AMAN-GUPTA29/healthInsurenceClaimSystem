/**
 * API Service Abstraction.
 *
 * All backend communication goes through this module.
 * React components must never make fetch() calls directly.
 *
 * Design:
 * - Base URL is read from environment (VITE_API_BASE_URL)
 * - All methods return typed responses
 * - Errors are wrapped in APIError for consistent handling
 */

import type {
  APIError,
  ClaimResponse,
  ClaimSubmissionRequest,
  ClaimTraceResponse,
  HealthResponse,
} from '../types'

// ── Configuration ─────────────────────────────────────────────────────────────

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

// ── HTTP Client ───────────────────────────────────────────────────────────────

class APIClientError extends Error {
  constructor(
    public readonly apiError: APIError,
    public readonly status: number
  ) {
    super(apiError.message)
    this.name = 'APIClientError'
  }
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE_URL}${path}`
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  })

  if (!response.ok) {
    let errorBody: APIError
    try {
      errorBody = await response.json()
    } catch {
      errorBody = {
        error: 'UNKNOWN_ERROR',
        message: `HTTP ${response.status}: ${response.statusText}`,
      }
    }
    throw new APIClientError(errorBody, response.status)
  }

  return response.json() as Promise<T>
}

// ── System Endpoints ──────────────────────────────────────────────────────────

export const systemApi = {
  health: (): Promise<HealthResponse> =>
    request<HealthResponse>('/api/v1/health'),
}

// ── Trace Endpoints ───────────────────────────────────────────────────────────

export const traceApi = {
  getClaimTrace: (claimId: string): Promise<ClaimTraceResponse> =>
    request<ClaimTraceResponse>(`/api/v1/claims/${encodeURIComponent(claimId)}/trace`),
}

// ── Claims Endpoints (Phase 2A) ─────────────────────────────────────────────
//
// Phase 2A only covers claim validation, document verification, and
// cross-document validation — a claim that clears all three comes back
// with status "PROCESSING" and no decision yet.

export const claimsApi = {
  submit: (submission: ClaimSubmissionRequest): Promise<ClaimResponse> =>
    request<ClaimResponse>('/api/v1/claims', {
      method: 'POST',
      body: JSON.stringify(submission),
    }),
  get: (claimId: string): Promise<ClaimResponse> =>
    request<ClaimResponse>(`/api/v1/claims/${encodeURIComponent(claimId)}`),
}

export { APIClientError }
export type { APIError }
