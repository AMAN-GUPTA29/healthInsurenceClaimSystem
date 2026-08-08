/**
 * Tests for TraceViewer — verifies it renders all supported trace statuses
 * and the associated content (message, duration, confidence, error, metadata).
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { TraceViewer } from './TraceViewer'
import type { TraceEvent } from '../types'

function makeEvent(overrides: Partial<TraceEvent> = {}): TraceEvent {
  return {
    event_id: 'evt-1',
    trace_id: 'trace-1',
    claim_id: 'CLM-1',
    component: 'CLAIM_VALIDATION',
    event_type: 'COMPLETED',
    message: '',
    timestamp: '2024-11-01T10:00:00Z',
    metadata: {},
    ...overrides,
  }
}

describe('TraceViewer', () => {
  it('renders the empty state when there are no events', () => {
    render(<TraceViewer events={[]} />)
    expect(screen.getByTestId('trace-viewer-empty')).toBeInTheDocument()
  })

  it('renders a custom empty message', () => {
    render(<TraceViewer events={[]} emptyMessage="Nothing to see yet" />)
    expect(screen.getByText('Nothing to see yet')).toBeInTheDocument()
  })

  it('renders a STARTED event', () => {
    render(<TraceViewer events={[makeEvent({ event_id: 'e1', event_type: 'STARTED' })]} />)
    expect(screen.getByTestId('trace-event-STARTED')).toBeInTheDocument()
  })

  it('renders a COMPLETED event', () => {
    render(<TraceViewer events={[makeEvent({ event_id: 'e2', event_type: 'COMPLETED' })]} />)
    expect(screen.getByTestId('trace-event-COMPLETED')).toBeInTheDocument()
  })

  it('renders a FAILED event', () => {
    render(<TraceViewer events={[makeEvent({ event_id: 'e3', event_type: 'FAILED' })]} />)
    expect(screen.getByTestId('trace-event-FAILED')).toBeInTheDocument()
  })

  it('renders a WARNING event', () => {
    render(<TraceViewer events={[makeEvent({ event_id: 'e4', event_type: 'WARNING' })]} />)
    expect(screen.getByTestId('trace-event-WARNING')).toBeInTheDocument()
  })

  it('renders a SKIPPED event', () => {
    render(<TraceViewer events={[makeEvent({ event_id: 'e5', event_type: 'SKIPPED' })]} />)
    expect(screen.getByTestId('trace-event-SKIPPED')).toBeInTheDocument()
  })

  it('renders all five statuses together in order', () => {
    const events: TraceEvent[] = [
      makeEvent({ event_id: 'a', event_type: 'STARTED', component: 'CLAIM_VALIDATION' }),
      makeEvent({ event_id: 'b', event_type: 'COMPLETED', component: 'DOCUMENT_VERIFICATION' }),
      makeEvent({ event_id: 'c', event_type: 'WARNING', component: 'DOCUMENT_EXTRACTION' }),
      makeEvent({ event_id: 'd', event_type: 'FAILED', component: 'POLICY_ENGINE' }),
      makeEvent({ event_id: 'e', event_type: 'SKIPPED', component: 'DECISION_GENERATION' }),
    ]
    render(<TraceViewer events={events} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(5)
  })

  it('formats the component name in title case', () => {
    render(<TraceViewer events={[makeEvent({ component: 'DOCUMENT_VERIFICATION' })]} />)
    expect(screen.getByText('Document Verification')).toBeInTheDocument()
  })

  it('renders the event message when present', () => {
    render(<TraceViewer events={[makeEvent({ message: 'validated submission successfully' })]} />)
    expect(screen.getByText('validated submission successfully')).toBeInTheDocument()
  })

  it('renders duration when present', () => {
    render(<TraceViewer events={[makeEvent({ duration_ms: 245 })]} />)
    expect(screen.getByText(/245 ms/)).toBeInTheDocument()
  })

  it('renders confidence as a percentage when present', () => {
    render(<TraceViewer events={[makeEvent({ confidence: 0.93 })]} />)
    expect(screen.getByText(/93%/)).toBeInTheDocument()
  })

  it('does not render confidence when absent', () => {
    render(<TraceViewer events={[makeEvent({ confidence: undefined })]} />)
    expect(screen.queryByText(/confidence/)).not.toBeInTheDocument()
  })

  it('renders error details for a FAILED event', () => {
    render(
      <TraceViewer
        events={[
          makeEvent({
            event_type: 'FAILED',
            error: {
              error_type: 'DocumentUnreadableError',
              code: 'DOCUMENT_UNREADABLE',
              message: 'too blurry to read',
              recoverable: true,
            },
          }),
        ]}
      />
    )
    expect(screen.getByRole('alert')).toHaveTextContent('DocumentUnreadableError')
    expect(screen.getByRole('alert')).toHaveTextContent('too blurry to read')
    expect(screen.getByRole('alert')).toHaveTextContent('recoverable, pipeline continued')
  })

  it('renders metadata as chips', () => {
    render(
      <TraceViewer
        events={[makeEvent({ metadata: { document_type: 'PRESCRIPTION', quality: 'GOOD' } })]}
      />
    )
    expect(screen.getByText(/document_type: PRESCRIPTION/)).toBeInTheDocument()
    expect(screen.getByText(/quality: GOOD/)).toBeInTheDocument()
  })
})
