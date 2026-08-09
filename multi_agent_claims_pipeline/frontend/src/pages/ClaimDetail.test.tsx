/**
 * Tests for ClaimDetail's document-results rendering — verifies the
 * AI-determined Type/Quality/Patient/Confidence values come from the
 * backend response, never hardcoded in the frontend.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import '@testing-library/jest-dom'
import { ClaimDetail } from './ClaimDetail'
import { claimsApi } from '../services/api'
import { useClaimTrace } from '../hooks/useClaimTrace'
import type { ClaimResponse } from '../types'

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
})
