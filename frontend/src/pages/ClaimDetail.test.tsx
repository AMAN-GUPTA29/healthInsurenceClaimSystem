/**
 * Tests for ClaimDetail's document-results rendering — verifies the
 * AI-determined Type/Quality/Patient/Confidence values come from the
 * backend response, never hardcoded in the frontend.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import '@testing-library/jest-dom'
import { ClaimDetail } from './ClaimDetail'
import { claimsApi } from '../services/api'
import { useClaimTrace } from '../hooks/useClaimTrace'
import type {
  ClaimDecision,
  ClaimResponse,
  DocumentExtractionResult,
  FinancialCalculationResult,
  FraudAnalysisResult,
  PolicyEvaluationResult,
} from '../types'

vi.mock('../services/api', () => ({
  claimsApi: { get: vi.fn() },
  APIClientError: class APIClientError extends Error {
    apiError: { message: string }
    constructor(apiError: { message: string }) {
      super(apiError.message)
      this.apiError = apiError
    }
  },
}))

vi.mock('../hooks/useClaimTrace', () => ({
  useClaimTrace: vi.fn(() => ({ events: [], loading: false })),
}))

function renderAt(claimId: string) {
  return render(
    <MemoryRouter initialEntries={[`/claims/${claimId}`]}>
      <Routes>
        <Route path="/claims/:claimId" element={<ClaimDetail />} />
      </Routes>
    </MemoryRouter>
  )
}

const baseClaim: ClaimResponse = {
  claim_id: 'CLM-TEST01',
  member_id: 'EMP001',
  policy_id: 'PLUM_GHI_2024',
  claim_category: 'CONSULTATION',
  treatment_date: '2024-11-01',
  claimed_amount: 1500,
  status: 'PROCESSING',
  documents: [],
  created_at: '2024-11-01T00:00:00Z',
  updated_at: '2024-11-01T00:00:00Z',
}

describe('ClaimDetail — document results', () => {
  beforeEach(() => {
    vi.mocked(claimsApi.get).mockReset()
    vi.mocked(useClaimTrace).mockReturnValue({ events: [], loading: false, error: null, refetch: vi.fn() })
  })

  it('renders AI-determined type, quality, patient, and confidence for each document', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      documents: [
        {
          file_id: 'abc',
          file_name: 'prescription.jpg',
          document_type: 'PRESCRIPTION',
          quality: 'GOOD',
          patient_name: 'Rajesh Kumar',
          confidence: 0.94,
          processing_status: 'PROCESSED',
        },
        {
          file_id: 'def',
          file_name: 'hospital_bill.pdf',
          document_type: 'HOSPITAL_BILL',
          quality: 'GOOD',
          patient_name: 'Rajesh Kumar',
          confidence: 0.91,
          processing_status: 'PROCESSED',
        },
      ],
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('prescription.jpg')).toBeInTheDocument())
    expect(screen.getAllByTestId('document-result-card')).toHaveLength(2)
    expect(screen.getAllByText('Rajesh Kumar')).toHaveLength(2)
    expect(screen.getByText('0.94')).toBeInTheDocument()
    expect(screen.getByText('0.91')).toBeInTheDocument()
    expect(screen.getByText('HOSPITAL BILL')).toBeInTheDocument()
  })

  it('shows a placeholder for documents not yet processed', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      documents: [
        {
          file_id: 'abc',
          file_name: 'pending.jpg',
          processing_status: 'PENDING',
        },
      ],
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('pending.jpg')).toBeInTheDocument())
    const card = screen.getByTestId('document-result-card')
    expect(card).toHaveTextContent('—')
  })

  it('never renders a storage_reference even if present on the payload', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      documents: [
        {
          file_id: 'abc',
          file_name: 'rx.jpg',
          document_type: 'PRESCRIPTION',
          processing_status: 'PROCESSED',
          // @ts-expect-error — simulating a leaked field the backend should never send
          storage_reference: 'CLM-TEST01/abc.jpg',
        },
      ],
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('rx.jpg')).toBeInTheDocument())
    expect(screen.queryByText(/CLM-TEST01\/abc\.jpg/)).not.toBeInTheDocument()
  })

  it('shows "Not processed" with an explanation, not "Processing…", when document verification was skipped because claim validation failed', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'BLOCKED',
      stopped_at: 'CLAIM_VALIDATION',
      user_message: 'Member ID EMP999 was not found on this policy.',
      documents: [
        {
          file_id: 'abc',
          file_name: 'prescription.jpg',
          processing_status: 'PENDING',
        },
      ],
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('prescription.jpg')).toBeInTheDocument())
    expect(screen.queryByText(/Processing…/)).not.toBeInTheDocument()
    expect(screen.getByText(/Not processed/)).toBeInTheDocument()
    expect(
      screen.getByText('Document verification was skipped because claim validation failed.')
    ).toBeInTheDocument()
  })

  it('still shows "Processing…" for a PENDING document when the pipeline did not stop at claim validation', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      documents: [
        {
          file_id: 'abc',
          file_name: 'prescription.jpg',
          processing_status: 'PENDING',
        },
      ],
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('prescription.jpg')).toBeInTheDocument())
    expect(screen.getByText(/Processing…/)).toBeInTheDocument()
    expect(screen.queryByTestId('document-skip-reason')).not.toBeInTheDocument()
  })
})

describe('ClaimDetail — extracted information (Phase 2B)', () => {
  beforeEach(() => {
    vi.mocked(claimsApi.get).mockReset()
    vi.mocked(useClaimTrace).mockReturnValue({ events: [], loading: false, error: null, refetch: vi.fn() })
  })

  const prescriptionExtraction: DocumentExtractionResult = {
    file_id: 'abc',
    document_type: 'PRESCRIPTION',
    quality: 'GOOD',
    patient: { name: 'Rajesh Kumar' },
    document_date: '2024-11-01',
    source: 'ai',
    extraction: {
      document_type: 'PRESCRIPTION',
      confidence: 0.94,
      warnings: [],
      evidence: [],
      patient: { name: 'Rajesh Kumar' },
      prescription_date: '2024-11-01',
      doctor: { name: 'Dr. Arun Sharma', registration_number: 'KA/45678/2015' },
      diagnosis: 'Viral Fever',
      treatment: undefined,
      medications: [{ name: 'Paracetamol', strength: '650mg', dosage: '1-1-1', frequency: undefined, duration: '5 days', route: undefined, instructions: undefined }],
      investigations: ['CBC', 'Dengue NS1'],
      signature_present: true,
      stamp_present: true,
    },
  }

  it('shows an "Extracted Information" toggle only when the document has an extraction, and expands it on click', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      documents: [
        {
          file_id: 'abc',
          file_name: 'prescription.jpg',
          document_type: 'PRESCRIPTION',
          quality: 'GOOD',
          patient_name: 'Rajesh Kumar',
          confidence: 0.94,
          processing_status: 'PROCESSED',
          extraction: prescriptionExtraction,
        },
        {
          file_id: 'def',
          file_name: 'no_extraction.jpg',
          document_type: 'PRESCRIPTION',
          processing_status: 'PROCESSED',
        },
      ],
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('prescription.jpg')).toBeInTheDocument())
    expect(screen.getAllByTestId('toggle-extracted-information')).toHaveLength(1)
    expect(screen.queryByTestId('extracted-information')).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('toggle-extracted-information'))

    expect(screen.getByTestId('extracted-information')).toBeInTheDocument()
    expect(screen.getByText('Dr. Arun Sharma')).toBeInTheDocument()
    expect(screen.getByText('Viral Fever')).toBeInTheDocument()
    expect(screen.getByText(/Paracetamol 650mg/)).toBeInTheDocument()
    expect(screen.getByText('CBC, Dengue NS1')).toBeInTheDocument()
  })

  it('renders extraction warnings when present', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      documents: [
        {
          file_id: 'abc',
          file_name: 'blurry.jpg',
          document_type: 'PRESCRIPTION',
          processing_status: 'PROCESSED',
          extraction: {
            ...prescriptionExtraction,
            extraction: {
              ...prescriptionExtraction.extraction,
              warnings: ['Doctor registration number could not be read clearly.'],
            },
          },
        },
      ],
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('blurry.jpg')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('toggle-extracted-information'))

    expect(
      screen.getByText(/Doctor registration number could not be read clearly\./)
    ).toBeInTheDocument()
  })

  it('shows a failure reason for a document that was not extracted', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      documents: [
        {
          file_id: 'abc',
          file_name: 'corrupt.jpg',
          document_type: 'PRESCRIPTION',
          processing_status: 'PROCESSED',
        },
      ],
      extraction_result: {
        extractions: [],
        failures: [{ file_id: 'abc', document_type: 'PRESCRIPTION', reason: 'AI provider returned no structured extraction' }],
        skipped: [],
        has_failures: true,
      },
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('corrupt.jpg')).toBeInTheDocument())
    expect(screen.queryByTestId('toggle-extracted-information')).not.toBeInTheDocument()
    expect(screen.getByTestId('extraction-failure-reason')).toHaveTextContent(
      'AI provider returned no structured extraction'
    )
  })
})

describe('ClaimDetail — policy/financial/fraud (Phase 2C)', () => {
  beforeEach(() => {
    vi.mocked(claimsApi.get).mockReset()
    vi.mocked(useClaimTrace).mockReturnValue({ events: [], loading: false, error: null, refetch: vi.fn() })
  })

  const policyResult: PolicyEvaluationResult = {
    covered: true,
    coverage_category: 'CONSULTATION',
    findings: [
      { rule: 'CONSULTATION_COVERED', status: 'PASSED', evidence: 'opd_categories.consultation.covered = true', details: {} },
      { rule: 'SUBMISSION_DEADLINE', status: 'FAILED', evidence: 'Submitted 700 day(s) after treatment_date.', details: {} },
    ],
    passed_rules: ['CONSULTATION_COVERED'],
    failed_rules: ['SUBMISSION_DEADLINE'],
    warnings: [],
    requires_pre_authorization: false,
    pre_authorization_provided: false,
    waiting_period_applies: false,
    exclusion_applies: false,
    is_network_hospital: false,
    sub_limit: 2000,
    per_claim_limit: 5000,
    annual_opd_limit: 50000,
    copay_percent: 10,
    network_discount_percent: 20,
    line_item_findings: [],
    confidence: 1.0,
  }

  const financialResult: FinancialCalculationResult = {
    claimed_amount: 1500,
    eligible_amount: 1500,
    network_discount_percent: 0,
    network_discount_amount: 0,
    amount_after_network_discount: 1500,
    sub_limit: 2000,
    sub_limit_applied: false,
    per_claim_limit: 5000,
    per_claim_limit_applied: false,
    annual_opd_limit: 50000,
    annual_limit_applied: false,
    amount_after_limits: 1500,
    copay_percent: 10,
    copay_amount: 150,
    payable_amount: 1350,
    currency: 'INR',
    calculation_steps: ['Claimed amount: 1500', 'Co-pay (10%) deducted: -150.00 -> 1350.00'],
    warnings: [],
    confidence: 1.0,
  }

  const fraudResult: FraudAnalysisResult = {
    risk_level: 'LOW',
    flags: [],
    deterministic_thresholds_triggered: [],
    same_day_claim_count: 1,
    monthly_claim_count: 1,
    is_high_value: false,
    requires_manual_review: false,
    confidence: 1.0,
  }

  it('renders policy, financial, and fraud sections from real backend values', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'PROCESSING',
      policy_evaluation_result: policyResult,
      financial_calculation_result: financialResult,
      fraud_analysis_result: fraudResult,
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('Policy Evaluation')).toBeInTheDocument())
    expect(screen.getByText('Covered')).toBeInTheDocument()
    expect(screen.getByText('Financial Calculation')).toBeInTheDocument()
    expect(screen.getByText('₹1,350.00')).toBeInTheDocument() // payable amount, from the backend, not computed here
    expect(screen.getByText('Fraud Analysis')).toBeInTheDocument()
    expect(screen.getByText('LOW Risk')).toBeInTheDocument()

    await userEvent.click(screen.getByTestId('toggle-policy-rules'))
    expect(screen.getByTestId('policy-rules-list')).toHaveTextContent('SUBMISSION DEADLINE')
  })

  it('does not render policy/financial/fraud sections when results are absent', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({ ...baseClaim })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('CLM-TEST01')).toBeInTheDocument())
    expect(screen.queryByText('Policy Evaluation')).not.toBeInTheDocument()
    expect(screen.queryByText('Financial Calculation')).not.toBeInTheDocument()
    expect(screen.queryByText('Fraud Analysis')).not.toBeInTheDocument()
  })

  it('shows fraud flags and manual-review badge when triggered', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      fraud_analysis_result: {
        ...fraudResult,
        risk_level: 'HIGH',
        requires_manual_review: true,
        flags: [{ code: 'SAME_DAY_CLAIMS_LIMIT_EXCEEDED', message: '4 claims exceeds limit of 2.', evidence: {} }],
      },
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('HIGH Risk')).toBeInTheDocument())
    expect(screen.getByText('Manual Review Recommended')).toBeInTheDocument()
    expect(screen.getByTestId('fraud-flags')).toHaveTextContent('4 claims exceeds limit of 2.')
  })
})

describe('ClaimDetail — decision (Phase 2D)', () => {
  beforeEach(() => {
    vi.mocked(claimsApi.get).mockReset()
    vi.mocked(useClaimTrace).mockReturnValue({ events: [], loading: false, error: null, refetch: vi.fn() })
  })

  function makeDecision(overrides: Partial<ClaimDecision> = {}): ClaimDecision {
    return {
      claim_id: 'CLM-TEST01',
      member_id: 'EMP001',
      policy_id: 'PLUM_GHI_2024',
      category: 'CONSULTATION',
      treatment_date: '2024-11-01',
      claimed_amount: 1500,
      decision: 'APPROVED',
      approved_amount: 1350,
      rejection_reasons: [],
      line_item_decisions: [],
      reason_code: 'APPROVED_FULL',
      confidence_score: 0.95,
      explanation: 'The claim is covered under the applicable policy terms.',
      member_facing_message: 'Your claim has been approved.',
      component_traces: [],
      has_component_failures: false,
      manual_review_recommended: false,
      fraud_signals: [],
      degraded_components: [],
      decided_at: '2024-11-01T00:00:00Z',
      ...overrides,
    }
  }

  it('renders APPROVED prominently with approved amount and confidence, from real backend values only', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'DECIDED',
      decision: makeDecision({
        explanation_detail: {
          member_summary: 'Your claim has been approved.',
          operations_summary: 'Consultation claim approved after 10% copay.',
          key_reasons: ['Fully covered under policy terms'],
          deductions: ['10% co-pay: -Rs. 150.00'],
          policy_findings: ['CONSULTATION_COVERED: PASSED'],
          warnings: [],
          source: 'AI',
          degraded: false,
          confidence: 0.95,
          ai_calls: [],
        },
      }),
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByTestId('decision-label')).toBeInTheDocument())
    expect(screen.getByTestId('decision-label')).toHaveTextContent('Approved')
    expect(screen.getByTestId('decision-claimed-amount')).toHaveTextContent('₹1,500.00')
    expect(screen.getByTestId('decision-approved-amount')).toHaveTextContent('₹1,350.00')
    expect(screen.getByTestId('decision-confidence')).toHaveTextContent('95%')
    // Claimed vs. approved must both be visible, plus a concise relationship.
    expect(screen.getByTestId('decision-amount-summary')).toHaveTextContent(
      '₹1,350.00 approved out of ₹1,500.00 claimed.'
    )
    expect(screen.getByTestId('decision-member-message')).toHaveTextContent('Your claim has been approved.')
    expect(screen.getByText('Fully covered under policy terms')).toBeInTheDocument()
    // Explanation was AI-generated — no "fallback" badge should render.
    expect(screen.queryByText(/deterministic fallback/)).not.toBeInTheDocument()
  })

  it('renders PARTIAL with line-item breakdown available via operations explanation', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      claim_category: 'DENTAL',
      status: 'DECIDED',
      decision: makeDecision({
        category: 'DENTAL',
        decision: 'PARTIAL',
        approved_amount: 8000,
        reason_code: 'PARTIAL_LINE_ITEM_EXCLUSION',
        line_item_decisions: [
          { description: 'Root Canal Treatment', claimed_amount: 8000, approved_amount: 8000, approved: true },
          {
            description: 'Teeth Whitening', claimed_amount: 4000, approved_amount: 0, approved: false,
            rejection_reason: 'EXCLUDED_PROCEDURE', notes: 'Cosmetic',
          },
        ],
      }),
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByTestId('decision-label')).toBeInTheDocument())
    expect(screen.getByTestId('decision-label')).toHaveTextContent('Partially Approved')
    expect(screen.getByTestId('decision-claimed-amount')).toHaveTextContent('₹1,500.00')
    expect(screen.getByTestId('decision-approved-amount')).toHaveTextContent('₹8,000.00')
    // Claimed vs. approved must both be visible, plus a concise relationship —
    // from decision.claimed_amount/approved_amount, never invented.
    expect(screen.getByTestId('decision-amount-summary')).toHaveTextContent(
      '₹8,000.00 approved out of ₹1,500.00 claimed.'
    )
  })

  it('renders REJECTED with rejection-reason badges, and the claimed amount alongside the ₹0 approved amount', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'DECIDED',
      decision: makeDecision({
        claimed_amount: 7500,
        decision: 'REJECTED',
        approved_amount: 0,
        reason_code: 'REJECTED_WAITING_PERIOD',
        rejection_reasons: ['WAITING_PERIOD'],
        member_facing_message: 'A policy waiting period applies and has not yet elapsed.',
      }),
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByTestId('decision-label')).toBeInTheDocument())
    expect(screen.getByTestId('decision-label')).toHaveTextContent('Rejected')
    // The known-confusing case: ₹0 approved must never appear without the
    // claimed amount right alongside it.
    expect(screen.getByTestId('decision-claimed-amount')).toHaveTextContent('₹7,500.00')
    expect(screen.getByTestId('decision-approved-amount')).toHaveTextContent('₹0.00')
    expect(screen.getByTestId('decision-amount-summary')).toHaveTextContent(
      '₹0.00 approved out of ₹7,500.00 claimed.'
    )
    expect(screen.getByText('WAITING PERIOD')).toBeInTheDocument()
  })

  it('renders MANUAL_REVIEW with a distinct badge', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'DECIDED',
      decision: makeDecision({
        decision: 'MANUAL_REVIEW',
        approved_amount: 1350,
        reason_code: 'MANUAL_REVIEW_FRAUD',
        fraud_signals: ['SAME_DAY_CLAIMS_LIMIT_EXCEEDED'],
        confidence_score: 0.5,
      }),
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByTestId('decision-label')).toBeInTheDocument())
    expect(screen.getByTestId('decision-label')).toHaveTextContent('Manual Review')
    expect(screen.getByTestId('decision-confidence')).toHaveTextContent('50%')
  })

  it('shows a fallback badge and degraded-component warning when explanation generation degraded', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'DECIDED',
      decision: makeDecision({
        degraded_components: ['FRAUD_ANALYSIS'],
        manual_review_recommended: true,
        explanation_detail: {
          member_summary: 'Your claim has been approved.',
          operations_summary: 'Approved; fraud analysis degraded during processing.',
          key_reasons: [],
          deductions: [],
          policy_findings: [],
          warnings: ['Explanation generated via deterministic fallback — FRAUD_ANALYSIS degraded during processing.'],
          source: 'FALLBACK',
          degraded: true,
          confidence: 0.6,
          ai_calls: [],
        },
      }),
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByTestId('decision-label')).toBeInTheDocument())
    expect(screen.getByText(/Explanation: deterministic fallback/)).toBeInTheDocument()
    expect(screen.getByText('Manual Review Recommended')).toBeInTheDocument()
    expect(screen.getByText(/FRAUD_ANALYSIS degraded during processing/)).toBeInTheDocument()
  })

  it('does not render a decision section for a BLOCKED claim with no decision', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'BLOCKED',
      stopped_at: 'CROSS_DOCUMENT_VALIDATION',
      user_message: 'The documents appear to belong to different patients.',
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByText('CLM-TEST01')).toBeInTheDocument())
    expect(screen.queryByTestId('decision-label')).not.toBeInTheDocument()
    expect(screen.getByText('⚠ Document Problem')).toBeInTheDocument()
  })

  it('expands the operations explanation on click, showing policy findings and deductions', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'DECIDED',
      decision: makeDecision({
        // Realistically, once ExplanationAgent succeeds the pipeline
        // overwrites `decision.explanation` with this same
        // operations_summary text (see pipeline.py Stage 9) — set both
        // to the same string here, matching that real invariant.
        explanation: 'Consultation claim approved after 10% copay deduction.',
        explanation_detail: {
          member_summary: 'Your claim has been approved.',
          operations_summary: 'Consultation claim approved after 10% copay deduction.',
          key_reasons: ['Fully covered'],
          deductions: ['10% co-pay: -Rs. 150.00'],
          policy_findings: ['CONSULTATION_COVERED: PASSED'],
          warnings: [],
          source: 'AI',
          degraded: false,
          confidence: 0.95,
          ai_calls: [],
        },
      }),
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByTestId('decision-label')).toBeInTheDocument())
    expect(screen.queryByTestId('decision-operations-detail')).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('toggle-operations-explanation'))

    expect(screen.getByTestId('decision-operations-detail')).toHaveTextContent(
      'Consultation claim approved after 10% copay deduction.'
    )
    expect(screen.getByText('CONSULTATION_COVERED: PASSED')).toBeInTheDocument()
    expect(screen.getByText('10% co-pay: -Rs. 150.00')).toBeInTheDocument()
  })

  it('still renders the trace section alongside the decision', async () => {
    vi.mocked(claimsApi.get).mockResolvedValue({
      ...baseClaim,
      status: 'DECIDED',
      decision: makeDecision(),
    })

    renderAt('CLM-TEST01')

    await waitFor(() => expect(screen.getByTestId('decision-label')).toBeInTheDocument())
    expect(screen.getByText('Trace')).toBeInTheDocument()
  })
})
