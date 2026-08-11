/**
 * ClaimHistory page — real, persisted claims from the backend database
 * (GET /api/v1/claims via useClaims), newest first. No mock data, no
 * hardcoded rows — an empty list genuinely means no claims have been
 * submitted yet.
 */

import { Link, useNavigate } from 'react-router-dom'
import { useClaims } from '../hooks/useClaims'
import type { ClaimStatus, DecisionType } from '../types'

const CARD: React.CSSProperties = {
  background: 'rgba(30, 41, 59, 0.6)',
  border: '1px solid rgba(51, 65, 85, 0.5)',
  borderRadius: '16px',
  padding: '28px',
  backdropFilter: 'blur(8px)',
}

function fmtAmount(amount?: number): string {
  if (amount == null) return '—'
  return `₹${amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short', day: 'numeric' })
}

const STATUS_STYLE: Record<ClaimStatus, { bg: string; fg: string; label: string }> = {
  SUBMITTED: { bg: 'rgba(99,102,241,0.15)', fg: '#a5b4fc', label: 'Submitted' },
  VALIDATING: { bg: 'rgba(99,102,241,0.15)', fg: '#a5b4fc', label: 'Validating' },
  BLOCKED: { bg: 'rgba(239,68,68,0.15)', fg: '#fca5a5', label: 'Blocked' },
  DOCUMENTS_PENDING: { bg: 'rgba(245,158,11,0.15)', fg: '#fcd34d', label: 'Documents Needed' },
  PROCESSING: { bg: 'rgba(99,102,241,0.15)', fg: '#a5b4fc', label: 'Processing' },
  AWAITING_REVIEW: { bg: 'rgba(245,158,11,0.15)', fg: '#fcd34d', label: 'Awaiting Review' },
  DECIDED: { bg: 'rgba(34,197,94,0.15)', fg: '#86efac', label: 'Decided' },
  CLOSED: { bg: 'rgba(100,116,139,0.15)', fg: '#94a3b8', label: 'Closed' },
}

const DECISION_STYLE: Record<DecisionType, { bg: string; fg: string; label: string; icon: string }> = {
  APPROVED: { bg: 'rgba(34,197,94,0.15)', fg: '#86efac', label: 'Approved', icon: '✓' },
  PARTIAL: { bg: 'rgba(245,158,11,0.15)', fg: '#fcd34d', label: 'Partial', icon: '◐' },
  REJECTED: { bg: 'rgba(239,68,68,0.15)', fg: '#fca5a5', label: 'Rejected', icon: '✕' },
  MANUAL_REVIEW: { bg: 'rgba(168,85,247,0.15)', fg: '#d8b4fe', label: 'Manual Review', icon: '⧗' },
  PENDING: { bg: 'rgba(100,116,139,0.15)', fg: '#94a3b8', label: 'Pending', icon: '○' },
}

function Pill({ bg, fg, children }: { bg: string; fg: string; children: React.ReactNode }) {
  return (
    <span
      style={{
        background: bg,
        color: fg,
        borderRadius: '6px',
        padding: '4px 10px',
        fontSize: '12px',
        fontWeight: 700,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

export function ClaimHistory() {
  const { claims, loading, error, refetch } = useClaims()
  const navigate = useNavigate()

  return (
    <div style={{ padding: '40px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: '0 0 6px', fontSize: '24px', fontWeight: 700, color: '#f1f5f9' }}>
            Claim History
          </h1>
          <p style={{ margin: 0, color: '#64748b', fontSize: '14px' }}>
            Real claims submitted to this system, newest first.
          </p>
        </div>
        <button
          onClick={refetch}
          disabled={loading}
          style={{
            background: 'rgba(99, 102, 241, 0.15)',
            border: '1px solid rgba(99, 102, 241, 0.3)',
            borderRadius: '8px',
            color: '#a5b4fc',
            padding: '8px 16px',
            cursor: loading ? 'not-allowed' : 'pointer',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          {loading ? '⟳ Loading...' : '↻ Refresh'}
        </button>
      </div>

      <div style={CARD}>
        {loading && claims.length === 0 && (
          <div style={{ textAlign: 'center', padding: '32px', color: '#64748b', fontSize: '14px' }}>
            <div style={{ fontSize: '28px', marginBottom: '8px' }}>⟳</div>
            Loading claims...
          </div>
        )}

        {!loading && error && (
          <div
            data-testid="claim-history-error"
            style={{
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              borderRadius: '10px',
              padding: '16px',
              color: '#fca5a5',
            }}
          >
            <strong>⚠ Unable to load claims. Please try again.</strong>
            <p style={{ margin: '8px 0 0', fontSize: '13px', opacity: 0.8 }}>{error}</p>
          </div>
        )}

        {!loading && !error && claims.length === 0 && (
          <div data-testid="claim-history-empty" style={{ textAlign: 'center', padding: '32px', color: '#64748b' }}>
            <div style={{ fontSize: '28px', marginBottom: '8px' }}>📋</div>
            <p style={{ margin: '0 0 12px', fontSize: '14px' }}>No claims have been submitted yet.</p>
            <Link to="/claims/new" style={{ color: '#a5b4fc', fontSize: '13px', fontWeight: 600 }}>
              Submit the first claim →
            </Link>
          </div>
        )}

        {!loading && !error && claims.length > 0 && (
          <div style={{ overflowX: 'auto' }} data-testid="claim-history-table">
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: '#64748b', fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  <th style={{ padding: '0 12px 12px 0', fontWeight: 600 }}>Claim ID</th>
                  <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Member</th>
                  <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Category</th>
                  <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Treatment Date</th>
                  <th style={{ padding: '0 12px 12px', fontWeight: 600, textAlign: 'right' }}>Claimed</th>
                  <th style={{ padding: '0 12px 12px', fontWeight: 600, textAlign: 'right' }}>Approved</th>
                  <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Status</th>
                  <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Decision</th>
                  <th style={{ padding: '0 0 12px', fontWeight: 600 }}>Submitted</th>
                </tr>
              </thead>
              <tbody>
                {claims.map(claim => {
                  const status = STATUS_STYLE[claim.status]
                  const decision = claim.decision ? DECISION_STYLE[claim.decision] : null
                  return (
                    <tr
                      key={claim.claim_id}
                      data-testid="claim-history-row"
                      onClick={() => navigate(`/claims/${claim.claim_id}`)}
                      style={{ borderTop: '1px solid rgba(51, 65, 85, 0.5)', cursor: 'pointer' }}
                    >
                      <td style={{ padding: '12px 12px 12px 0' }}>
                        <Link
                          to={`/claims/${claim.claim_id}`}
                          onClick={e => e.stopPropagation()}
                          style={{ color: '#a5b4fc', fontWeight: 600 }}
                        >
                          {claim.claim_id}
                        </Link>
                      </td>
                      <td style={{ padding: '12px', color: '#cbd5e1' }}>{claim.member_id}</td>
                      <td style={{ padding: '12px', color: '#cbd5e1' }}>
                        {claim.claim_category.replace(/_/g, ' ')}
                      </td>
                      <td style={{ padding: '12px', color: '#cbd5e1' }}>{claim.treatment_date}</td>
                      <td style={{ padding: '12px', color: '#cbd5e1', textAlign: 'right' }}>
                        {fmtAmount(claim.claimed_amount)}
                      </td>
                      <td style={{ padding: '12px', color: '#e2e8f0', textAlign: 'right', fontWeight: 600 }}>
                        {fmtAmount(claim.approved_amount)}
                      </td>
                      <td style={{ padding: '12px' }}>
                        <Pill bg={status.bg} fg={status.fg}>{status.label}</Pill>
                      </td>
                      <td style={{ padding: '12px' }}>
                        {decision ? (
                          <Pill bg={decision.bg} fg={decision.fg}>{decision.icon} {decision.label}</Pill>
                        ) : (
                          <span style={{ color: '#475569' }}>—</span>
                        )}
                      </td>
                      <td style={{ padding: '12px 0', color: '#64748b' }}>{fmtDate(claim.created_at)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
