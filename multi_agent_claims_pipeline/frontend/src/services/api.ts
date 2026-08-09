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
  ClaimSubmissionFields,
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

  return handleResponse<T>(response)
}

/**
 * For multipart/form-data submissions (real file uploads). Deliberately
 * does NOT set a Content-Type header — the browser must set it itself
 * (including the multipart boundary), which it can only do when it
 * controls the request body directly.
 */
async function requestMultipart<T>(path: string, formData: FormData): Promise<T> {
  const url = `${API_BASE_URL}${path}`
  const response = await fetch(url, { method: 'POST', body: formData })
  return handleResponse<T>(response)
}

async function handleResponse<T>(response: Response): Promise<T> {
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
  /**
   * Submits claim metadata + real uploaded files as multipart/form-data —
   * matching the backend's POST /api/v1/claims contract exactly (see
   * backend app/api/v1/claims.py). `files` must be real File objects from
   * a file input; there is no document-type field to set here — the AI
   * determines each document's type from its actual content.
   */
  submit: (fields: ClaimSubmissionFields, files: File[]): Promise<ClaimResponse> => {
    const formData = new FormData()
    formData.append('member_id', fields.member_id)
    formData.append('policy_id', fields.policy_id)
    formData.append('claim_category', fields.claim_category)
    formData.append('treatment_date', fields.treatment_date)
    formData.append('claimed_amount', String(fields.claimed_amount))
    if (fields.hospital_name) formData.append('hospital_name', fields.hospital_name)
    if (fields.ytd_claims_amount !== undefined) {
      formData.append('ytd_claims_amount', String(fields.ytd_claims_amount))
    }
    for (const file of files) {
      formData.append('documents', file, file.name)
    }
    return requestMultipart<ClaimResponse>('/api/v1/claims', formData)
  },
  get: (claimId: string): Promise<ClaimResponse> =>
    request<ClaimResponse>(`/api/v1/claims/${encodeURIComponent(claimId)}`),
}

export { APIClientError }
export type { APIError }
