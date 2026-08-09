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
  | 'BLOCKED'
  | 'DOCUMENTS_PENDING'
  | 'PROCESSING'
  | 'AWAITING_REVIEW'
  | 'DECIDED'
  | 'CLOSED'

export type DocumentQuality = 'GOOD' | 'LOW_QUALITY' | 'PARTIAL' | 'UNREADABLE' | 'UNKNOWN'

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
  quality: DocumentQuality
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

// ── Claim Processing (Phase 2A) ──────────────────────────────────────────────
//
// Mirrors app/domain/verification.py and app/api/v1/schemas.py. No final
// decision fields here yet — APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW are
// later phases.

export interface ValidationIssue {
  code: string
  message: string
  field?: string
  recoverable: boolean
}

export interface ValidationResult {
  valid: boolean
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
}

export type DocumentVerificationStatus = 'PASS' | 'BLOCKED' | 'NEEDS_RESUBMISSION'

export interface DocumentClassification {
  file_id: string
  document_type: DocumentType
  quality: DocumentQuality
  patient_name?: string
  confidence?: number
  source: 'ai' | 'fixture'
}

export interface DocumentVerificationResult {
  status: DocumentVerificationStatus
  required_documents: DocumentType[]
  received_documents: DocumentType[]
  missing_documents: DocumentType[]
  wrong_documents: DocumentType[]
  quality_issues: DocumentClassification[]
  classifications: DocumentClassification[]
  user_message: string
  confidence?: number
}

export type CrossDocumentValidationStatus = 'PASS' | 'BLOCKED'

export interface CrossDocumentValidationResult {
  status: CrossDocumentValidationStatus
  patient_names: Record<string, string>
  user_message: string
  confidence?: number
}

// ── Claim submission (Phase 2A correction: real file upload) ────────────────
//
// POST /api/v1/claims is multipart/form-data — claim metadata as form
// fields, real PDF/JPEG/PNG files under the "documents" field. There is no
// JSON request type to mirror here; see services/api.ts's claimsApi.submit,
// which builds the FormData directly. (The JSON-bodied ClaimSubmissionRequest
// shape still exists backend-side, for the evaluation/fixture path only —
// app/services/document_input_adapter.py — but the frontend never uses it.)

export interface ClaimSubmissionFields {
  member_id: string
  policy_id: string
  claim_category: ClaimCategory
  treatment_date: string
  claimed_amount: number
  hospital_name?: string
  ytd_claims_amount?: number
}

export type DocumentProcessingStatus = 'PENDING' | 'PROCESSED' | 'FAILED'

export interface ClaimDocumentSummary {
  file_id: string
  file_name?: string
  mime_type?: string
  size_bytes?: number
  document_type?: DocumentType
  quality?: DocumentQuality
  patient_name?: string
  confidence?: number
  processing_status: DocumentProcessingStatus
}

export interface ClaimResponse {
  claim_id: string
  member_id: string
  policy_id: string
  claim_category: ClaimCategory
  treatment_date: string
  claimed_amount: number
  status: ClaimStatus
  trace_id?: string
  stopped_at?: string
  user_message?: string
  processing_time_ms?: number
  documents: ClaimDocumentSummary[]
  validation_result?: ValidationResult
  document_verification_result?: DocumentVerificationResult
  cross_document_validation_result?: CrossDocumentValidationResult
  created_at: string
  updated_at: string
}

// ── Trace / Observability ────────────────────────────────────────────────────
//
// Mirrors app/domain/trace.py. Keep this block in sync with the backend
// enums/models rather than letting the shape drift or duplicating it
// elsewhere in the frontend.

export type TraceComponent =
  | 'CLAIM_VALIDATION'
  | 'DOCUMENT_VERIFICATION'
  | 'DOCUMENT_EXTRACTION'
  | 'CROSS_DOCUMENT_VALIDATION'
  | 'POLICY_ENGINE'
  | 'FRAUD_ANALYSIS'
  | 'FINANCIAL_CALCULATION'
  | 'DECISION_GENERATION'
  | 'EXPLANATION'
  | 'PIPELINE'

export type TraceEventType =
  | 'STARTED'
  | 'COMPLETED'
  | 'FAILED'
  | 'SKIPPED'
  | 'WARNING'

export interface TraceErrorInfo {
  error_type: string
  code?: string
  message: string
  recoverable: boolean
}

export interface AITraceMetadata {
  provider?: string
  model?: string
  latency_ms?: number
  input_tokens?: number
  output_tokens?: number
}

export interface TraceEvent {
  event_id: string
  trace_id: string
  claim_id: string
  component: TraceComponent
  event_type: TraceEventType
  message: string
  timestamp: string
  duration_ms?: number
  confidence?: number
  metadata: Record<string, unknown>
  error?: TraceErrorInfo
  ai_metadata?: AITraceMetadata
  sequence?: number
}

export interface ClaimTraceResponse {
  claim_id: string
  count: number
  events: TraceEvent[]
}

// ── API Error ─────────────────────────────────────────────────────────────────

export interface APIError {
  error: string
  message: string
  details?: Record<string, unknown>
  recoverable?: boolean
}
