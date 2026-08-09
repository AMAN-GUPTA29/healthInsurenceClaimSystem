/**
 * ClaimDetail page — claim information, processing status, validation
 * result, actionable message, and the full trace (via the existing
 * Phase 1 TraceViewer + useClaimTrace, unmodified).
 */

import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { claimsApi, APIClientError } from '../services/api'
import { useClaimTrace } from '../hooks/useClaimTrace'
import { TraceViewer } from '../components/TraceViewer'
import type { ClaimResponse, ClaimStatus } from '../types'

const CARD: React.CSSProperties = {
  background: 'rgba(30, 41, 59, 0.6)',
  border: '1px solid rgba(51, 65, 85, 0.5)',
  borderRadius: '16px',
  padding: '28px',
  backdropFilter: 'blur(8px)',
  marginBottom: '20px',
}

const STATUS_STYLE: Record<ClaimStatus, { bg: string; fg: string; border: string; label: string }> = {
  SUBMITTED: { bg: 'rgba(99,102,241,0.12)', fg: '#a5b4fc', border: 'rgba(99,102,241,0.3)', label: 'Submitted' },
  VALIDATING: { bg: 'rgba(99,102,241,0.12)', fg: '#a5b4fc', border: 'rgba(99,102,241,0.3)', label: 'Validating' },
  BLOCKED: { bg: 'rgba(239,68,68,0.12)', fg: '#fca5a5', border: 'rgba(239,68,68,0.3)', label: 'Blocked' },
  DOCUMENTS_PENDING: { bg: 'rgba(245,158,11,0.12)', fg: '#fcd34d', border: 'rgba(245,158,11,0.3)', label: 'Documents Needed' },
  PROCESSING: { bg: 'rgba(34,197,94,0.12)', fg: '#86efac', border: 'rgba(34,197,94,0.3)', label: 'Processing' },
  AWAITING_REVIEW: { bg: 'rgba(245,158,11,0.12)', fg: '#fcd34d', border: 'rgba(245,158,11,0.3)', label: 'Awaiting Review' },
  DECIDED: { bg: 'rgba(34,197,94,0.12)', fg: '#86efac', border: 'rgba(34,197,94,0.3)', label: 'Decided' },
  CLOSED: { bg: 'rgba(100,116,139,0.12)', fg: '#94a3b8', border: 'rgba(100,116,139,0.3)', label: 'Closed' },
}

function StatusBadge({ status }: { status: ClaimStatus }) {
  const s = STATUS_STYLE[status]
  return (
    <span
      style={{
        background: s.bg,
        color: s.fg,
        border: `1px solid ${s.border}`,
        borderRadius: '8px',
        padding: '5px 14px',
        fontSize: '13px',
        fontWeight: 700,
      }}
    >
      {s.label}
    </span>
  )
}

const PROCESSING_STATUS_LABEL: Record<string, string> = {
  PENDING: '⟳ Processing…',
  PROCESSED: '✓ Processed',
  FAILED: '✕ Processing failed',
}

function DocumentCard({ doc }: { doc: ClaimResponse['documents'][number] }) {
  return (
    <div
      data-testid="document-result-card"
      style={{
        background: 'rgba(15, 23, 42, 0.5)',
        border: '1px solid rgba(51, 65, 85, 0.6)',
        borderRadius: '10px',
        padding: '14px 16px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <span style={{ fontSize: '13px', fontWeight: 600, color: '#e2e8f0' }}>
          {doc.file_name ?? doc.file_id}
        </span>
        <span style={{ fontSize: '11px', color: '#64748b' }}>
          {PROCESSING_STATUS_LABEL[doc.processing_status] ?? doc.processing_status}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', fontSize: '12px' }}>
        <div>
          <div style={{ color: '#64748b', marginBottom: '2px' }}>Type</div>
          <div style={{ color: '#cbd5e1' }}>
            {doc.document_type ? doc.document_type.replace(/_/g, ' ') : '—'}
          </div>
        </div>
        <div>
          <div style={{ color: '#64748b', marginBottom: '2px' }}>Quality</div>
          <div style={{ color: '#cbd5e1' }}>{doc.quality ? doc.quality.replace(/_/g, ' ') : '—'}</div>
        </div>
        <div>
          <div style={{ color: '#64748b', marginBottom: '2px' }}>Patient</div>
          <div style={{ color: '#cbd5e1' }}>{doc.patient_name || '—'}</div>
        </div>
        <div>
          <div style={{ color: '#64748b', marginBottom: '2px' }}>Confidence</div>
          <div style={{ color: '#cbd5e1' }}>
            {doc.confidence != null ? doc.confidence.toFixed(2) : '—'}
          </div>
        </div>
      </div>
    </div>
  )
}

function DocList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '4px' }}>
        {title}
      </div>
      <ul style={{ margin: 0, paddingLeft: '18px', color: '#cbd5e1', fontSize: '13px' }}>
        {items.map((item, idx) => (
          <li key={`${item}-${idx}`}>{item.replace(/_/g, ' ')}</li>
        ))}
      </ul>
    </div>
  )
}

export function ClaimDetail() {
  const { claimId } = useParams<{ claimId: string }>()
  const [claim, setClaim] = useState<ClaimResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { events, loading: traceLoading } = useClaimTrace(claimId ?? null)

  useEffect(() => {
    if (!claimId) return
    let cancelled = false
    setLoading(true)
    claimsApi
      .get(claimId)
      .then(data => {
        if (!cancelled) setClaim(data)
      })
      .catch(err => {
        if (cancelled) return
        setError(err instanceof APIClientError ? err.apiError.message : 'Could not load this claim.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [claimId])

  if (loading) {
    return <div style={{ padding: '40px 0', color: '#64748b' }}>Loading claim…</div>
  }

  if (error || !claim) {
    return (
      <div style={{ padding: '40px 0' }}>
        <div style={{ ...CARD, borderColor: 'rgba(239,68,68,0.3)' }}>
          <strong style={{ color: '#fca5a5' }}>⚠ {error ?? 'Claim not found.'}</strong>
          <p style={{ marginTop: '10px' }}>
            <Link to="/claims/new" style={{ color: '#a5b4fc' }}>
              Submit a new claim
            </Link>
          </p>
        </div>
      </div>
    )
  }

  const dvr = claim.document_verification_result
  const cdvr = claim.cross_document_validation_result
  const hasProblem = claim.status === 'BLOCKED' || claim.status === 'DOCUMENTS_PENDING'

  return (
    <div style={{ padding: '40px 0', maxWidth: '900px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: '0 0 6px', fontSize: '22px', fontWeight: 700, color: '#f1f5f9' }}>
            {claim.claim_id}
          </h1>
          <p style={{ margin: 0, color: '#64748b', fontSize: '13px' }}>
            {claim.claim_category.replace(/_/g, ' ')} · Member {claim.member_id} · ₹{claim.claimed_amount}
          </p>
        </div>
        <StatusBadge status={claim.status} />
      </div>

      {hasProblem && claim.user_message && (
        <div
          style={{
            ...CARD,
            background: 'rgba(239, 68, 68, 0.08)',
            borderColor: 'rgba(239, 68, 68, 0.3)',
          }}
        >
          <h2 style={{ margin: '0 0 12px', fontSize: '15px', fontWeight: 700, color: '#fca5a5' }}>
            ⚠ Document Problem
          </h2>
          <p style={{ margin: '0 0 16px', color: '#fecaca', fontSize: '14px', lineHeight: 1.6 }}>
            {claim.user_message}
          </p>
          {dvr && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <DocList title="You uploaded" items={dvr.received_documents} />
              <DocList title="Required" items={dvr.required_documents} />
            </div>
          )}
          {cdvr && Object.keys(cdvr.patient_names).length > 0 && (
            <div style={{ marginTop: '8px' }}>
              <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', marginBottom: '6px' }}>
                Patient names found
              </div>
              {Object.entries(cdvr.patient_names).map(([doc, name]) => (
                <div key={doc} style={{ fontSize: '13px', color: '#fecaca' }}>
                  {doc}: <strong>{name}</strong>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!hasProblem && claim.user_message && (
        <div style={{ ...CARD, background: 'rgba(34, 197, 94, 0.08)', borderColor: 'rgba(34, 197, 94, 0.3)' }}>
          <p style={{ margin: 0, color: '#bbf7d0', fontSize: '14px' }}>✓ {claim.user_message}</p>
        </div>
      )}

      <div style={CARD}>
        <h2 style={{ margin: '0 0 16px', fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>
          Documents
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {claim.documents.map(doc => (
            <DocumentCard key={doc.file_id} doc={doc} />
          ))}
        </div>
      </div>

      <div style={CARD}>
        <h2 style={{ margin: '0 0 16px', fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>
          Processing Pipeline
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
          <StageCard label="Claim Validation" passed={claim.validation_result?.valid} />
          <StageCard
            label="Document Verification"
            passed={dvr ? dvr.status === 'PASS' : undefined}
            note={dvr?.status}
          />
          <StageCard
            label="Cross-Document Validation"
            passed={cdvr ? cdvr.status === 'PASS' : undefined}
            note={cdvr?.status}
          />
        </div>
      </div>

      <div style={CARD}>
        <h2 style={{ margin: '0 0 16px', fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>
          Trace
        </h2>
        {traceLoading && events.length === 0 ? (
          <p style={{ color: '#64748b', fontSize: '13px' }}>Loading trace…</p>
        ) : (
          <TraceViewer events={events} />
        )}
      </div>
    </div>
  )
}

function StageCard({
  label,
  passed,
  note,
}: {
  label: string
  passed?: boolean
  note?: string
}) {
  const icon = passed === undefined ? '○' : passed ? '✓' : '✕'
  const color = passed === undefined ? '#64748b' : passed ? '#86efac' : '#fca5a5'
  return (
    <div
      style={{
        background: 'rgba(15, 23, 42, 0.5)',
        border: '1px solid rgba(51, 65, 85, 0.6)',
        borderRadius: '10px',
        padding: '14px',
        textAlign: 'center',
      }}
    >
      <div style={{ fontSize: '20px', color, marginBottom: '6px' }}>{icon}</div>
      <div style={{ fontSize: '12px', color: '#cbd5e1', fontWeight: 600 }}>{label}</div>
      {note && (
        <div style={{ fontSize: '11px', color: '#64748b', marginTop: '4px' }}>
          {note.replace(/_/g, ' ')}
        </div>
      )}
    </div>
  )
}
