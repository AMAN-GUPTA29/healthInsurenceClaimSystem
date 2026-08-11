/**
 * Tests for Dashboard — verifies the pipeline overview reflects the
 * current (Phase 3-complete) system, not the stale Phase 2A prototype,
 * and that system status comes from the real GET /api/v1/health response.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import { Dashboard } from './Dashboard'
import { useHealth } from '../hooks/useHealth'
import type { HealthResponse } from '../types'

vi.mock('../hooks/useHealth', () => ({
  useHealth: vi.fn(),
}))

const baseHealth: HealthResponse = {
  status: 'healthy',
  version: '0.1.0',
  environment: 'development',
  timestamp: '2024-11-01T00:00:00Z',
  ai_provider: { provider: 'gemini', model: 'gemini-flash-latest', status: 'configured' },
  database: 'connected',
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  )
}

describe('Dashboard', () => {
  beforeEach(() => {
    vi.mocked(useHealth).mockReturnValue({
      health: baseHealth,
      loading: false,
      error: null,
      refetch: vi.fn(),
    })
  })

  it('lists all 10 current pipeline stages, each shown as Live', () => {
    renderDashboard()
    const stages = [
      'Claim Validation',
      'Document Verification',
      'Cross-Document Validation',
      'Document Extraction',
      'Policy Evaluation',
      'Financial Calculation',
      'Fraud Analysis',
      'Decision Generation',
      'Explanation',
      'Trace & Observability',
    ]
    for (const stage of stages) {
      expect(screen.getAllByText(new RegExp(stage)).length).toBeGreaterThanOrEqual(1)
    }
    // Every stage renders "Live" — none says Planned/Pending/Coming Soon.
    expect(screen.getAllByText('✅ Live')).toHaveLength(10)
  })

  it('does not contain stale Phase 2A wording or "Planned"/"Pending" stage labels', () => {
    renderDashboard()
    expect(screen.queryByText(/Phase 2A/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Planned/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Coming Soon/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Policy Evaluation Pending/)).not.toBeInTheDocument()
  })

  it('renders real system health values from the backend, not invented ones', () => {
    renderDashboard()
    expect(screen.getByText('healthy')).toBeInTheDocument()
    expect(screen.getByText('gemini')).toBeInTheDocument()
    expect(screen.getByText('gemini-flash-latest')).toBeInTheDocument()
    expect(screen.getByText('connected')).toBeInTheDocument()
  })

  it('shows a connecting state while health is loading', () => {
    vi.mocked(useHealth).mockReturnValue({ health: null, loading: true, error: null, refetch: vi.fn() })
    renderDashboard()
    expect(screen.getByText(/Connecting to backend/)).toBeInTheDocument()
  })

  it('shows an error state when the backend is unreachable', () => {
    vi.mocked(useHealth).mockReturnValue({
      health: null,
      loading: false,
      error: 'Failed to fetch',
      refetch: vi.fn(),
    })
    renderDashboard()
    expect(screen.getByText(/Cannot reach backend/)).toBeInTheDocument()
  })

  it('links to Submit Claim, Claim History, and Evaluation Report', () => {
    renderDashboard()
    expect(screen.getByRole('link', { name: /Submit a Claim/ })).toHaveAttribute('href', '/claims/new')
    expect(screen.getByRole('link', { name: /Claim History/ })).toHaveAttribute('href', '/claims')
    expect(screen.getByRole('link', { name: /Evaluation Report/ })).toHaveAttribute('href', '/reports')
  })
})
