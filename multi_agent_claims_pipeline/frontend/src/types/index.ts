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

// Document types DocumentExtractionAgent has a schema for (Phase 2B).
// DIAGNOSTIC_REPORT / PRE_AUTH_LETTER / UNKNOWN are classified but not
// extracted — see app/domain/extraction.py SUPPORTED_EXTRACTION_TYPES.
export type ExtractableDocumentType =
  | 'PRESCRIPTION'
  | 'HOSPITAL_BILL'
  | 'LAB_REPORT'
  | 'PHARMACY_BILL'
  | 'DENTAL_REPORT'
  | 'DISCHARGE_SUMMARY'

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

// ── Document Extraction (Phase 2B) ───────────────────────────────────────────
//
// Mirrors app/domain/extraction.py. DocumentExtractionAgent runs after
// cross-document validation and produces one typed, document-type-specific
// payload per document — never a raw/unvalidated blob.

export interface PatientInfo {
  name?: string
  age?: number
  gender?: string
  date_of_birth?: string
  member_id?: string
}

export interface DoctorInfo {
  name?: string
  registration_number?: string
  specialization?: string
  hospital_or_clinic?: string
}

export interface EvidenceItem {
  field: string
  quote: string
}

export interface Medication {
  name: string
  strength?: string
  dosage?: string
  frequency?: string
  duration?: string
  route?: string
  instructions?: string
}

export interface LineItem {
  description: string
  quantity?: number
  unit_price?: number
  amount?: number
}

export interface LabTestResult {
  test_name: string
  result?: string
  unit?: string
  reference_range?: string
  abnormal_flag?: boolean
}

export interface PharmacyItem {
  medicine_name: string
  batch_number?: string
  expiry_date?: string
  quantity?: number
  mrp?: number
  discount?: number
  amount?: number
}

interface ExtractionBaseFields {
  confidence: number
  warnings: string[]
  evidence: EvidenceItem[]
}

export interface PrescriptionExtraction extends ExtractionBaseFields {
  document_type: 'PRESCRIPTION'
  patient: PatientInfo
  prescription_date?: string
  doctor: DoctorInfo
  diagnosis?: string
  treatment?: string
  medications: Medication[]
  investigations: string[]
  signature_present?: boolean
  stamp_present?: boolean
}

export interface HospitalBillExtraction extends ExtractionBaseFields {
  document_type: 'HOSPITAL_BILL'
  patient_name?: string
  hospital_name?: string
  bill_number?: string
  bill_date?: string
  admission_date?: string
  discharge_date?: string
  doctor: DoctorInfo
  line_items: LineItem[]
  subtotal?: number
  discount?: number
  tax?: number
  total?: number
  currency: string
}

export interface LabReportExtraction extends ExtractionBaseFields {
  document_type: 'LAB_REPORT'
  patient: PatientInfo
  referring_doctor?: string
  sample_date?: string
  report_date?: string
  tests: LabTestResult[]
  laboratory_name?: string
  pathologist_name?: string
  registration_number?: string
}

export interface PharmacyBillExtraction extends ExtractionBaseFields {
  document_type: 'PHARMACY_BILL'
  patient_name?: string
  pharmacy_name?: string
  bill_number?: string
  bill_date?: string
  items: PharmacyItem[]
  subtotal?: number
  tax?: number
  total?: number
}

export interface DentalReportExtraction extends ExtractionBaseFields {
  document_type: 'DENTAL_REPORT'
  patient_name?: string
  dentist: DoctorInfo
  date_on_document?: string
  diagnosis?: string
  procedure?: string
  treatment?: string
  amount?: number
  notes?: string
}

export interface DischargeSummaryExtraction extends ExtractionBaseFields {
  document_type: 'DISCHARGE_SUMMARY'
  patient_name?: string
  hospital_name?: string
  admission_date?: string
  discharge_date?: string
  diagnosis?: string
  procedure_or_treatment?: string
  doctor: DoctorInfo
  discharge_instructions?: string
  total_amount?: number
}

export type ExtractionPayload =
  | PrescriptionExtraction
  | HospitalBillExtraction
  | LabReportExtraction
  | PharmacyBillExtraction
  | DentalReportExtraction
  | DischargeSummaryExtraction

export interface DocumentExtractionResult {
  file_id: string
  document_type: DocumentType
  quality: DocumentQuality
  patient: PatientInfo
  document_date?: string
  source: 'ai'
  extraction: ExtractionPayload
}

export interface DocumentExtractionFailure {
  file_id: string
  document_type?: DocumentType
  reason: string
}

export interface ClaimExtractionResult {
  extractions: DocumentExtractionResult[]
  failures: DocumentExtractionFailure[]
  skipped: string[]
  confidence?: number
  has_failures: boolean
}

// ── Policy Evaluation / Financial Calculation / Fraud Analysis (Phase 2C) ───
//
// Mirrors app/domain/policy_evaluation.py, app/domain/models.py's
// FinancialBreakdown, and app/domain/fraud.py. None of these decide
// APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW — that's Phase 2D's
// DecisionGenerationAgent, still unimplemented; `financial_calculation_result
// .payable_amount` is "what would be payable if approved," not an approval.

export type PolicyRuleStatus = 'PASSED' | 'FAILED' | 'WARNING' | 'NOT_APPLICABLE'

export interface PolicyRuleFinding {
  rule: string
  status: PolicyRuleStatus
  evidence: string
  details: Record<string, unknown>
}

export interface LineItemPolicyFinding {
  description: string
  amount?: number
  excluded: boolean
  reason?: string
}

export interface PolicyEvaluationResult {
  covered: boolean
  coverage_category: ClaimCategory
  findings: PolicyRuleFinding[]
  passed_rules: string[]
  failed_rules: string[]
  warnings: string[]
  requires_pre_authorization: boolean
  pre_authorization_provided: boolean
  waiting_period_applies: boolean
  exclusion_applies: boolean
  is_network_hospital?: boolean
  sub_limit?: number
  per_claim_limit?: number
  annual_opd_limit?: number
  copay_percent: number
  network_discount_percent: number
  line_item_findings: LineItemPolicyFinding[]
  confidence: number
}

export interface FinancialCalculationResult {
  claimed_amount: number
  eligible_amount: number
  network_discount_percent: number
  network_discount_amount: number
  amount_after_network_discount: number
  sub_limit?: number
  sub_limit_applied: boolean
  per_claim_limit?: number
  per_claim_limit_applied: boolean
  annual_opd_limit?: number
  annual_limit_applied: boolean
  amount_after_limits: number
  copay_percent: number
  copay_amount: number
  payable_amount: number
  currency: string
  calculation_steps: string[]
  warnings: string[]
  confidence: number
}

export type FraudRiskLevel = 'LOW' | 'MEDIUM' | 'HIGH'

export interface FraudFlag {
  code: string
  message: string
  evidence: Record<string, unknown>
}

export interface FraudAnalysisResult {
  risk_level: FraudRiskLevel
  flags: FraudFlag[]
  deterministic_thresholds_triggered: string[]
  same_day_claim_count: number
  monthly_claim_count: number
  is_high_value: boolean
  requires_manual_review: boolean
  ai_risk_score?: number
  confidence: number
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
  extraction?: DocumentExtractionResult
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
  extraction_result?: ClaimExtractionResult
  policy_evaluation_result?: PolicyEvaluationResult
  financial_calculation_result?: FinancialCalculationResult
  fraud_analysis_result?: FraudAnalysisResult
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
