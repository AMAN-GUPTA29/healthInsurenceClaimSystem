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
import type { ClaimResponse, DocumentExtractionResult } from '../types'

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
