/**
 * Tests for ClaimHistory — verifies it renders real backend data (never
 * mock/hardcoded claims) and handles loading/empty/error states.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import { ClaimHistory } from './ClaimHistory'
import { useClaims } from '../hooks/useClaims'
import type { ClaimSummary } from '../types'

vi.mock('../hooks/useClaims', () => ({
  useClaims: vi.fn(),
}))

function renderHistory() {
  return render(
    <MemoryRouter>
      <ClaimHistory />
    </MemoryRouter>
  )
}

const sampleClaims: ClaimSummary[] = [
  {
    claim_id: 'CLM-AAAA0001',
    member_id: 'EMP001',
    claim_category: 'CONSULTATION',
    treatment_date: '2024-11-01',
    claimed_amount: 1500,
    status: 'DECIDED',
    decision: 'APPROVED',
    approved_amount: 1350,
    created_at: '2024-11-02T10:00:00Z',
  },
  {
    claim_id: 'CLM-BBBB0002',
    member_id: 'EMP003',
    claim_category: 'CONSULTATION',
    treatment_date: '2024-10-20',
    claimed_amount: 7500,
    status: 'DECIDED',
    decision: 'REJECTED',
    approved_amount: 0,
    created_at: '2024-11-01T09:00:00Z',
  },
]

describe('ClaimHistory', () => {
  beforeEach(() => {
    vi.mocked(useClaims).mockReset()
  })

  it('shows a loading state while claims are being fetched', () => {
    vi.mocked(useClaims).mockReturnValue({ claims: [], loading: true, error: null, refetch: vi.fn() })
    renderHistory()
    expect(screen.getByText(/Loading claims/)).toBeInTheDocument()
  })

  it('shows an empty state when no claims exist, never a silent empty table', () => {
    vi.mocked(useClaims).mockReturnValue({ claims: [], loading: false, error: null, refetch: vi.fn() })
    renderHistory()
    expect(screen.getByTestId('claim-history-empty')).toBeInTheDocument()
    expect(screen.getByText(/No claims have been submitted yet/)).toBeInTheDocument()
    expect(screen.queryByTestId('claim-history-table')).not.toBeInTheDocument()
  })

  it('shows an error state when the backend is unreachable', () => {
    vi.mocked(useClaims).mockReturnValue({
      claims: [],
      loading: false,
      error: 'Network error',
      refetch: vi.fn(),
    })
    renderHistory()
    expect(screen.getByTestId('claim-history-error')).toBeInTheDocument()
    expect(screen.getByText(/Unable to load claims/)).toBeInTheDocument()
  })

  it('renders real claims from the backend with claim ID, member, category, amounts, status, and decision', () => {
    vi.mocked(useClaims).mockReturnValue({ claims: sampleClaims, loading: false, error: null, refetch: vi.fn() })
    renderHistory()

    expect(screen.getByText('CLM-AAAA0001')).toBeInTheDocument()
    expect(screen.getByText('CLM-BBBB0002')).toBeInTheDocument()
    expect(screen.getAllByText('EMP001')[0]).toBeInTheDocument()
    expect(screen.getByText('EMP003')).toBeInTheDocument()
    expect(screen.getAllByText(/Approved/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/Rejected/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByTestId('claim-history-row')).toHaveLength(2)
  })

  it('links each claim row to its detail page', () => {
    vi.mocked(useClaims).mockReturnValue({ claims: sampleClaims, loading: false, error: null, refetch: vi.fn() })
    renderHistory()
    const link = screen.getByRole('link', { name: 'CLM-AAAA0001' })
    expect(link).toHaveAttribute('href', '/claims/CLM-AAAA0001')
  })
})
