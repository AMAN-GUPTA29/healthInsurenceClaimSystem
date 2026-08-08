/**
 * Shared TypeScript types for the claims system.
 *
 * These mirror the backend Pydantic models.
 * All API responses should be typed using these definitions.
 */

// ── Enumerations ──────────────────────────────────────────────────────────────

export type DocumentType =
  | 'PRESCRIPTION'
  | 'HOSPITAL_BILL'
  | 'LAB_REPORT'
  | 'DIAGNOSTIC_REPORT'
  | 'PHARMACY_BILL'
  | 'DISCHARGE_SUMMARY'
  | 'DENTAL_REPORT'
  | 'PRE_AUTH_LETTER'
  | 'UNKNOWN'

export type ClaimCategory =
  | 'CONSULTATION'
  | 'DIAGNOSTIC'
  | 'PHARMACY'
  | 'DENTAL'
  | 'VISION'
  | 'ALTERNATIVE_MEDICINE'

export type DecisionType =
  | 'APPROVED'
  | 'PARTIAL'
  | 'REJECTED'
  | 'MANUAL_REVIEW'
  | 'PENDING'

export type ClaimStatus =
  | 'SUBMITTED'
  | 'VALIDATING'
  | 'DOCUMENTS_PENDING'
  | 'PROCESSING'
  | 'AWAITING_REVIEW'
  | 'DECIDED'
  | 'CLOSED'

export type RejectionReason =
  | 'WAITING_PERIOD'
  | 'PRE_AUTH_MISSING'
  | 'PER_CLAIM_EXCEEDED'
  | 'ANNUAL_LIMIT_EXCEEDED'
  | 'SUB_LIMIT_EXCEEDED'
  | 'EXCLUDED_CONDITION'
  | 'EXCLUDED_PROCEDURE'
  | 'MISSING_DOCUMENTS'
  | 'DOCUMENT_MISMATCH'
  | 'PATIENT_NOT_MEMBER'
  | 'LATE_SUBMISSION'
  | 'BELOW_MINIMUM_AMOUNT'
  | 'MEMBER_INACTIVE'
  | 'FRAUD_SUSPECTED'

// ── API Models ────────────────────────────────────────────────────────────────

export interface AIProviderStatus {
  provider: string
  model: string
  status: 'configured' | 'unconfigured' | 'error'
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy'
  version: string
  environment: string
  timestamp: string
  ai_provider: AIProviderStatus
  database: string
  uptime_note?: string
}

export interface DocumentMetadata {
  file_id: string
  file_name: string
  mime_type?: string
  size_bytes?: number
  declared_type?: DocumentType
  detected_type?: DocumentType
  quality: 'GOOD' | 'DEGRADED' | 'UNREADABLE' | 'UNKNOWN'
  uploaded_at: string
}

export interface LineItemDecision {
  description: string
  claimed_amount: number
  approved_amount: number
  approved: boolean
  rejection_reason?: RejectionReason
  notes?: string
}

export interface FinancialBreakdown {
  claimed_amount: number
  network_discount_percent: number
  network_discount_amount: number
  amount_after_network_discount: number
  copay_percent: number
  copay_amount: number
  sub_limit?: number
  sub_limit_applied: boolean
  per_claim_limit?: number
  per_claim_limit_applied: boolean
  approved_amount: number
  calculation_steps: string[]
}

export interface ComponentTrace {
  component_name: string
  status: 'completed' | 'failed' | 'skipped'
  duration_ms?: number
  error?: string
  notes?: string
}

export interface ClaimDecision {
  claim_id: string
  member_id: string
  policy_id: string
  category: ClaimCategory
  treatment_date: string
  claimed_amount: number
  decision: DecisionType
  approved_amount?: number
  rejection_reasons: RejectionReason[]
  line_item_decisions: LineItemDecision[]
  financial_breakdown?: FinancialBreakdown
  confidence_score: number
  explanation?: string
  member_facing_message?: string
  component_traces: ComponentTrace[]
  has_component_failures: boolean
  manual_review_recommended: boolean
  fraud_signals: string[]
  decided_at: string
  processing_time_ms?: number
}

// ── API Error ─────────────────────────────────────────────────────────────────

export interface APIError {
  error: string
  message: string
  details?: Record<string, unknown>
  recoverable?: boolean
}
