/**
 * Navigation tests — every sidebar link routes to a real page, no dead
 * links or placeholder "coming soon" nav items (Phase 4).
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom'
import App from './App'
import { useHealth } from './hooks/useHealth'
import { useClaims } from './hooks/useClaims'
import { useEvaluation } from './hooks/useEvaluation'

vi.mock('./hooks/useHealth', () => ({ useHealth: vi.fn() }))
vi.mock('./hooks/useClaims', () => ({ useClaims: vi.fn() }))
vi.mock('./hooks/useEvaluation', () => ({ useEvaluation: vi.fn() }))

describe('App navigation', () => {
  it('every sidebar nav item is a real, clickable link (no disabled placeholders)', () => {
    vi.mocked(useHealth).mockReturnValue({ health: null, loading: true, error: null, refetch: vi.fn() })
    render(<App />)
    const nav = within(screen.getByRole('navigation'))

    expect(nav.getByRole('link', { name: /Dashboard/ })).toHaveAttribute('href', '/')
    expect(nav.getByRole('link', { name: /Submit Claim/ })).toHaveAttribute('href', '/claims/new')
    expect(nav.getByRole('link', { name: /Claim History/ })).toHaveAttribute('href', '/claims')
    expect(nav.getByRole('link', { name: /Evaluation Report/ })).toHaveAttribute('href', '/reports')
  })

  it('does not show stale "Phase 2A" status text in the sidebar', () => {
    vi.mocked(useHealth).mockReturnValue({ health: null, loading: true, error: null, refetch: vi.fn() })
    render(<App />)
    expect(screen.queryByText(/Phase 2A/)).not.toBeInTheDocument()
  })

  it('navigating to Claim History renders the real ClaimHistory page', async () => {
    vi.mocked(useHealth).mockReturnValue({ health: null, loading: true, error: null, refetch: vi.fn() })
    vi.mocked(useClaims).mockReturnValue({ claims: [], loading: false, error: null, refetch: vi.fn() })
    render(<App />)
    const nav = within(screen.getByRole('navigation'))
    await userEvent.click(nav.getByRole('link', { name: /Claim History/ }))
    expect(await screen.findByText(/No claims have been submitted yet/)).toBeInTheDocument()
  })

  it('navigating to Evaluation Report renders the real Reports page', async () => {
    vi.mocked(useHealth).mockReturnValue({ health: null, loading: true, error: null, refetch: vi.fn() })
    vi.mocked(useEvaluation).mockReturnValue({ report: null, loading: true, error: null, refetch: vi.fn() })
    render(<App />)
    const nav = within(screen.getByRole('navigation'))
    await userEvent.click(nav.getByRole('link', { name: /Evaluation Report/ }))
    expect(await screen.findByText(/Running all 12 official cases/)).toBeInTheDocument()
  })
})
