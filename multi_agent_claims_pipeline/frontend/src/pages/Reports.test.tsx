/**
 * Tests for Reports — verifies the official evaluation summary/table
 * renders exactly what the backend's GET /api/v1/evaluation returns,
 * never a hardcoded "12/12" or fabricated case list.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import { Reports } from './Reports'
import { useEvaluation } from '../hooks/useEvaluation'
import type { EvaluationReportResponse } from '../types'

vi.mock('../hooks/useEvaluation', () => ({
  useEvaluation: vi.fn(),
}))

function renderReports() {
  return render(
    <MemoryRouter>
      <Reports />
    </MemoryRouter>
  )
}

const twelvePassReport: EvaluationReportResponse = {
  total: 12,
  passed: 12,
  all_passed: true,
  results: [
    { case_id: 'TC001', case_name: 'Wrong Document Uploaded', passed: true, reasons: [] },
    {
      case_id: 'TC004', case_name: 'Clean Consultation — Full Approval', passed: true, reasons: [],
      expected_decision: 'APPROVED', actual_decision: 'APPROVED',
      expected_approved_amount: 1350, actual_approved_amount: 1350,
    },
    {
      case_id: 'TC008', case_name: 'Per-Claim Limit Exceeded', passed: true, reasons: [],
      expected_decision: 'REJECTED', actual_decision: 'REJECTED',
    },
  ],
}

describe('Reports', () => {
  beforeEach(() => {
    vi.mocked(useEvaluation).mockReset()
  })

  it('explains the evaluation is a deterministic, fixture-based benchmark — not live AI/document testing', () => {
    vi.mocked(useEvaluation).mockReturnValue({ report: null, loading: true, error: null, refetch: vi.fn() })
    renderReports()
    expect(screen.getByText('Official Deterministic Evaluation')).toBeInTheDocument()
    expect(screen.getByText(/no live Gemini calls/)).toBeInTheDocument()
    expect(screen.getByText(/Policy rules/)).toBeInTheDocument()
    expect(screen.getByText(/Decision precedence/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Submit Claim' })).toHaveAttribute('href', '/claims/new')
  })

  it('shows a loading state while the evaluation runs', () => {
    vi.mocked(useEvaluation).mockReturnValue({ report: null, loading: true, error: null, refetch: vi.fn() })
    renderReports()
    expect(screen.getByText(/Running all 12 official cases/)).toBeInTheDocument()
  })

  it('shows an error state when the backend is unreachable', () => {
    vi.mocked(useEvaluation).mockReturnValue({
      report: null,
      loading: false,
      error: 'Network error',
      refetch: vi.fn(),
    })
    renderReports()
    expect(screen.getByTestId('reports-error')).toBeInTheDocument()
    expect(screen.getByText(/Unable to load the evaluation report/)).toBeInTheDocument()
  })

  it('renders the real 12/12 summary from the backend response, not a hardcoded value', () => {
    vi.mocked(useEvaluation).mockReturnValue({
      report: twelvePassReport,
      loading: false,
      error: null,
      refetch: vi.fn(),
    })
    renderReports()
    expect(screen.getByTestId('eval-summary')).toHaveTextContent('12 / 12')
    expect(screen.getByTestId('eval-summary')).toHaveTextContent('100%')
    expect(screen.getByText(/All cases pass/)).toBeInTheDocument()
  })

  it('renders one table row per case with expected/actual decision and amount', () => {
    vi.mocked(useEvaluation).mockReturnValue({
      report: twelvePassReport,
      loading: false,
      error: null,
      refetch: vi.fn(),
    })
    renderReports()
    const rows = screen.getAllByTestId('eval-case-row')
    expect(rows).toHaveLength(3)
    expect(screen.getByText('TC004')).toBeInTheDocument()
    expect(screen.getByText('TC008')).toBeInTheDocument()
    expect(screen.getAllByText('PASS', { exact: false }).length).toBeGreaterThan(0)
  })

  it('shows failing cases distinctly when the backend reports a failure', () => {
    vi.mocked(useEvaluation).mockReturnValue({
      report: {
        total: 12,
        passed: 11,
        all_passed: false,
        results: [
          {
            case_id: 'TC008', case_name: 'Per-Claim Limit Exceeded', passed: false,
            reasons: ['expected decision REJECTED, got APPROVED'],
            expected_decision: 'REJECTED', actual_decision: 'APPROVED',
          },
        ],
      },
      loading: false,
      error: null,
      refetch: vi.fn(),
    })
    renderReports()
    expect(screen.getByTestId('eval-summary')).toHaveTextContent('11 / 12')
    expect(screen.getByText(/1 case\(s\) failing/)).toBeInTheDocument()
    expect(screen.getByText(/expected decision REJECTED, got APPROVED/)).toBeInTheDocument()
  })
})
