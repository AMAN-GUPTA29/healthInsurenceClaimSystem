/**
 * TraceViewer — reusable trace-event timeline component.
 *
 * Renders a chronological sequence of TraceEvent records (status icon,
 * component, message, duration, confidence, error, metadata). Pure
 * presentation: it takes `events` as a prop and renders them — it does not
 * fetch data itself (see hooks/useClaimTrace.ts for that) and it does not
 * know what a "claim" is. This is infrastructure any future page
 * (claim detail, eval report) can drop in as-is.
 */

import type { TraceErrorInfo, TraceEvent, TraceEventType } from '../types'

// ── Status Presentation ──────────────────────────────────────────────────────

const STATUS_ICON: Record<TraceEventType, string> = {
  STARTED: '⟳',
  COMPLETED: '✓',
  FAILED: '✕',
  WARNING: '⚠',
  SKIPPED: '○',
}

const STATUS_COLOR: Record<TraceEventType, { fg: string; bg: string; border: string }> = {
  STARTED: { fg: '#a5b4fc', bg: 'rgba(99, 102, 241, 0.12)', border: 'rgba(99, 102, 241, 0.3)' },
  COMPLETED: { fg: '#86efac', bg: 'rgba(34, 197, 94, 0.12)', border: 'rgba(34, 197, 94, 0.3)' },
  FAILED: { fg: '#fca5a5', bg: 'rgba(239, 68, 68, 0.12)', border: 'rgba(239, 68, 68, 0.3)' },
  WARNING: { fg: '#fcd34d', bg: 'rgba(245, 158, 11, 0.12)', border: 'rgba(245, 158, 11, 0.3)' },
  SKIPPED: { fg: '#94a3b8', bg: 'rgba(100, 116, 139, 0.12)', border: 'rgba(100, 116, 139, 0.3)' },
}

function componentLabel(component: string): string {
  return component
    .toLowerCase()
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

function formatTime(timestamp: string): string {
  const d = new Date(timestamp)
  return Number.isNaN(d.getTime()) ? timestamp : d.toLocaleTimeString()
}

// ── Sub-components ───────────────────────────────────────────────────────────

function ErrorDetails({ error }: { error: TraceErrorInfo }) {
  return (
    <div
      role="alert"
      style={{
        marginTop: '6px',
        fontSize: '12px',
        color: '#fca5a5',
        background: 'rgba(239, 68, 68, 0.08)',
        border: '1px solid rgba(239, 68, 68, 0.2)',
        borderRadius: '6px',
        padding: '6px 10px',
      }}
    >
      <strong>{error.error_type}</strong>
      {error.code ? ` (${error.code})` : ''}: {error.message}
      {' — '}
      {error.recoverable ? 'recoverable, pipeline continued' : 'not recoverable'}
    </div>
  )
}

function MetadataChips({ metadata }: { metadata: Record<string, unknown> }) {
  const entries = Object.entries(metadata)
  if (entries.length === 0) return null
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: '6px', marginTop: '6px' }}>
      {entries.map(([key, value]) => (
        <span
          key={key}
          style={{
            fontSize: '11px',
            color: '#94a3b8',
            background: 'rgba(15, 23, 42, 0.6)',
            border: '1px solid rgba(51, 65, 85, 0.7)',
            borderRadius: '5px',
            padding: '2px 8px',
          }}
        >
          {key}: {String(value)}
        </span>
      ))}
    </div>
  )
}

function TraceEventRow({ event }: { event: TraceEvent }) {
  const color = STATUS_COLOR[event.event_type]
  return (
    <div
      data-testid={`trace-event-${event.event_type}`}
      role="listitem"
      style={{
        display: 'flex',
        gap: '12px',
        padding: '12px 14px',
        borderRadius: '10px',
        background: 'rgba(15, 23, 42, 0.5)',
        border: `1px solid ${color.border}`,
        borderLeft: `3px solid ${color.fg}`,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          flexShrink: 0,
          width: '24px',
          height: '24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: '50%',
          background: color.bg,
          color: color.fg,
          fontSize: '13px',
          fontWeight: 700,
        }}
      >
        {STATUS_ICON[event.event_type]}
      </span>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' as const }}>
          <span style={{ fontSize: '13px', fontWeight: 600, color: '#e2e8f0' }}>
            {componentLabel(event.component)}
          </span>
          <span
            style={{
              fontSize: '10px',
              fontWeight: 700,
              letterSpacing: '0.05em',
              textTransform: 'uppercase' as const,
              color: color.fg,
              background: color.bg,
              border: `1px solid ${color.border}`,
              borderRadius: '4px',
              padding: '1px 6px',
            }}
          >
            {event.event_type}
          </span>
          <span style={{ marginLeft: 'auto', fontSize: '11px', color: '#64748b' }}>
            {formatTime(event.timestamp)}
          </span>
        </div>

        {event.message && (
          <div style={{ fontSize: '12.5px', color: '#94a3b8', marginTop: '4px' }}>{event.message}</div>
        )}

        {(event.duration_ms != null || event.confidence != null) && (
          <div style={{ display: 'flex', gap: '12px', marginTop: '6px', fontSize: '11px', color: '#64748b' }}>
            {event.duration_ms != null && <span>⏱ {event.duration_ms.toFixed(0)} ms</span>}
            {event.confidence != null && <span>◎ confidence {(event.confidence * 100).toFixed(0)}%</span>}
          </div>
        )}

        {event.error && <ErrorDetails error={event.error} />}
        <MetadataChips metadata={event.metadata} />
      </div>
    </div>
  )
}

// ── TraceViewer ───────────────────────────────────────────────────────────────

export interface TraceViewerProps {
  events: TraceEvent[]
  emptyMessage?: string
}

export function TraceViewer({ events, emptyMessage = 'No trace events recorded yet.' }: TraceViewerProps) {
  if (events.length === 0) {
    return (
      <div
        data-testid="trace-viewer-empty"
        style={{
          textAlign: 'center' as const,
          padding: '24px',
          fontSize: '13px',
          color: '#64748b',
          border: '1px dashed rgba(51, 65, 85, 0.8)',
          borderRadius: '10px',
        }}
      >
        {emptyMessage}
      </div>
    )
  }

  return (
    <div data-testid="trace-viewer" role="list" style={{ display: 'flex', flexDirection: 'column' as const, gap: '8px' }}>
      {events.map(event => (
        <TraceEventRow key={event.event_id} event={event} />
      ))}
    </div>
  )
}
