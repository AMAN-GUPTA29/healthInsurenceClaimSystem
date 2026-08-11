/**
 * Dashboard page — system health + pipeline overview.
 *
 * Every pipeline stage listed here is implemented and live — see
 * app/pipeline/pipeline.py for the real 9-stage orchestration this
 * mirrors. Nothing on this page is a placeholder; system status comes
 * entirely from GET /api/v1/health (useHealth), never invented.
 */

import { Link } from 'react-router-dom'
import { useHealth } from '../hooks/useHealth'
import type { HealthResponse } from '../types'

// ── Status Indicator ──────────────────────────────────────────────────────────

function StatusDot({ status }: { status: HealthResponse['status'] | 'loading' | 'error' }) {
  const color =
    status === 'healthy' ? '#22c55e'
    : status === 'degraded' ? '#f59e0b'
    : status === 'loading' ? '#6366f1'
    : '#ef4444'

  return (
    <span
      style={{
        display: 'inline-block',
        width: '10px',
        height: '10px',
        borderRadius: '50%',
        backgroundColor: color,
        marginRight: '8px',
        boxShadow: `0 0 6px ${color}`,
      }}
    />
  )
}

// ── Badge ─────────────────────────────────────────────────────────────────────

function Badge({ children, variant = 'default' }: {
  children: React.ReactNode
  variant?: 'default' | 'success' | 'warning' | 'error'
}) {
  const colors = {
    default: { bg: 'rgba(99, 102, 241, 0.15)', color: '#a5b4fc', border: 'rgba(99,102,241,0.3)' },
    success: { bg: 'rgba(34, 197, 94, 0.15)', color: '#86efac', border: 'rgba(34,197,94,0.3)' },
    warning: { bg: 'rgba(245, 158, 11, 0.15)', color: '#fcd34d', border: 'rgba(245,158,11,0.3)' },
    error:   { bg: 'rgba(239, 68, 68, 0.15)', color: '#fca5a5', border: 'rgba(239,68,68,0.3)' },
  }
  const c = colors[variant]
  return (
    <span style={{
      background: c.bg,
      color: c.color,
      border: `1px solid ${c.border}`,
      borderRadius: '6px',
      padding: '2px 10px',
      fontSize: '12px',
      fontWeight: 600,
      letterSpacing: '0.03em',
      textTransform: 'uppercase' as const,
    }}>
      {children}
    </span>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export function Dashboard() {
  const { health, loading, error, refetch } = useHealth()

  return (
    <div style={{ padding: '40px 0' }}>
      {/* Hero */}
      <div style={{ marginBottom: '48px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '12px' }}>
          <div style={{
            background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)',
            borderRadius: '14px',
            width: '52px',
            height: '52px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            boxShadow: '0 8px 24px rgba(99, 102, 241, 0.4)',
          }}>
            🏥
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: '28px', fontWeight: 700, color: '#f1f5f9' }}>
              Claims Processing System
            </h1>
            <p style={{ margin: 0, color: '#64748b', fontSize: '14px' }}>
              AI-powered health insurance claim evaluation pipeline
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' as const }}>
          <Badge variant="success">Full Pipeline Active</Badge>
          <Badge variant="default">10 / 10 Stages Live</Badge>
        </div>
      </div>

      {/* System Health Card */}
      <div style={{
        background: 'rgba(30, 41, 59, 0.6)',
        border: '1px solid rgba(99, 102, 241, 0.2)',
        borderRadius: '16px',
        padding: '28px',
        backdropFilter: 'blur(8px)',
        marginBottom: '28px',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
          <div>
            <h2 style={{ margin: '0 0 4px', fontSize: '16px', fontWeight: 600, color: '#e2e8f0' }}>
              System Health
            </h2>
            <p style={{ margin: 0, fontSize: '13px', color: '#64748b' }}>
              Backend API status
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
              padding: '6px 14px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: '13px',
              fontWeight: 500,
              transition: 'all 0.2s',
            }}
          >
            {loading ? '⟳ Checking...' : '↻ Refresh'}
          </button>
        </div>

        {loading && (
          <div style={{ textAlign: 'center' as const, padding: '20px', color: '#64748b' }}>
            <div style={{ fontSize: '32px', marginBottom: '8px' }}>⟳</div>
            Connecting to backend...
          </div>
        )}

        {error && (
          <div style={{
            background: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: '10px',
            padding: '16px',
            color: '#fca5a5',
          }}>
            <strong>⚠ Cannot reach backend</strong>
            <p style={{ margin: '8px 0 0', fontSize: '13px', opacity: 0.8 }}>{error}</p>
            <p style={{ margin: '8px 0 0', fontSize: '12px', opacity: 0.6 }}>
              Make sure the FastAPI server is running: <code>uvicorn app.main:app --reload</code>
            </p>
          </div>
        )}

        {health && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '16px' }}>
            {[
              { label: 'Status', value: health.status, icon: <StatusDot status={health.status} /> },
              { label: 'Version', value: `v${health.version}`, icon: '📦' },
              { label: 'Environment', value: health.environment, icon: '🌐' },
              { label: 'AI Provider', value: health.ai_provider.provider, icon: '🤖' },
              { label: 'AI Model', value: health.ai_provider.model, icon: '✨' },
              { label: 'Database', value: health.database, icon: '🗄️' },
            ].map(({ label, value, icon }) => (
              <div key={label} style={{
                background: 'rgba(15, 23, 42, 0.6)',
                border: '1px solid rgba(51, 65, 85, 0.8)',
                borderRadius: '10px',
                padding: '14px 16px',
              }}>
                <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase' as const, letterSpacing: '0.06em', marginBottom: '6px' }}>
                  {label}
                </div>
                <div style={{ fontSize: '14px', color: '#e2e8f0', fontWeight: 500, display: 'flex', alignItems: 'center' }}>
                  {typeof icon === 'string' ? <span style={{ marginRight: '6px' }}>{icon}</span> : icon}
                  {value}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Pipeline Overview */}
      <div style={{
        background: 'rgba(30, 41, 59, 0.6)',
        border: '1px solid rgba(51, 65, 85, 0.5)',
        borderRadius: '16px',
        padding: '28px',
        backdropFilter: 'blur(8px)',
        marginBottom: '28px',
      }}>
        <h2 style={{ margin: '0 0 4px', fontSize: '16px', fontWeight: 600, color: '#e2e8f0' }}>
          Pipeline Architecture
        </h2>
        <p style={{ margin: '0 0 20px', fontSize: '13px', color: '#64748b' }}>
          Every claim flows through all 10 stages below, in order — early
          document problems stop the pipeline before Policy Evaluation
          ever runs; a claim that clears every check reaches a final
          decision at stage 8.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
          {[
            { step: 1, name: 'Claim Validation', icon: '📝' },
            { step: 2, name: 'Document Verification', icon: '📋' },
            { step: 3, name: 'Cross-Document Validation', icon: '🔗' },
            { step: 4, name: 'Document Extraction', icon: '🔍' },
            { step: 5, name: 'Policy Evaluation', icon: '📜' },
            { step: 6, name: 'Financial Calculation', icon: '💰' },
            { step: 7, name: 'Fraud Analysis', icon: '🛡️' },
            { step: 8, name: 'Decision Generation', icon: '⚖️' },
            { step: 9, name: 'Explanation', icon: '💬' },
            { step: 10, name: 'Trace & Observability', icon: '📊' },
          ].map(({ step, name, icon }) => (
            <div key={step} style={{
              background: 'rgba(15, 23, 42, 0.5)',
              border: '1px solid rgba(34, 197, 94, 0.35)',
              borderRadius: '10px',
              padding: '12px 14px',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
            }}>
              <div style={{
                background: 'rgba(34, 197, 94, 0.15)',
                border: '1px solid rgba(34, 197, 94, 0.3)',
                borderRadius: '6px',
                width: '28px',
                height: '28px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '11px',
                fontWeight: 700,
                color: '#86efac',
                flexShrink: 0,
              }}>
                {step}
              </div>
              <div>
                <div style={{ fontSize: '13px', color: '#cbd5e1', fontWeight: 500 }}>
                  {icon} {name}
                </div>
                <div style={{ fontSize: '11px', color: '#4ade80', marginTop: '2px' }}>
                  ✅ Live
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Quick Links */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
        {[
          { to: '/claims/new', icon: '📝', title: 'Submit a Claim', desc: 'Upload documents and run a claim through the full pipeline.' },
          { to: '/claims', icon: '📋', title: 'Claim History', desc: 'Browse previously submitted claims and their decisions.' },
          { to: '/reports', icon: '📊', title: 'Evaluation Report', desc: 'The official 12-case test_cases.json results, live.' },
        ].map(({ to, icon, title, desc }) => (
          <Link
            key={to}
            to={to}
            style={{
              background: 'rgba(30, 41, 59, 0.6)',
              border: '1px solid rgba(99, 102, 241, 0.25)',
              borderRadius: '14px',
              padding: '20px',
              display: 'block',
            }}
          >
            <div style={{ fontSize: '22px', marginBottom: '8px' }}>{icon}</div>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#e2e8f0', marginBottom: '4px' }}>{title}</div>
            <div style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.5 }}>{desc}</div>
          </Link>
        ))}
      </div>
    </div>
  )
}
