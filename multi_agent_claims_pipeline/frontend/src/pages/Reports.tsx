/**
 * Reports page — the official 12-case test_cases.json evaluation, live.
 *
 * Every number here comes from GET /api/v1/evaluation (useEvaluation),
 * which re-runs all 12 official cases through the real pipeline on each
 * request (app/evaluation/runner.py — the same code path as
 * `python scripts/run_eval.py`). Nothing is hardcoded or computed here.
 */

import { useEvaluation } from '../hooks/useEvaluation'
import type { EvalCaseResult } from '../types'

const CARD: React.CSSProperties = {
  background: 'rgba(30, 41, 59, 0.6)',
  border: '1px solid rgba(51, 65, 85, 0.5)',
  borderRadius: '16px',
  padding: '28px',
  backdropFilter: 'blur(8px)',
  marginBottom: '20px',
}

function fmtAmount(amount?: number): string {
  if (amount == null) return '—'
  return `₹${amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function ResultPill({ passed }: { passed: boolean }) {
  return (
    <span
      style={{
        background: passed ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
        color: passed ? '#86efac' : '#fca5a5',
        borderRadius: '6px',
        padding: '4px 12px',
        fontSize: '12px',
        fontWeight: 700,
      }}
    >
      {passed ? '✓ PASS' : '✕ FAIL'}
    </span>
  )
}

function CaseRow({ result }: { result: EvalCaseResult }) {
  return (
    <tr data-testid="eval-case-row" style={{ borderTop: '1px solid rgba(51, 65, 85, 0.5)' }}>
      <td style={{ padding: '12px 12px 12px 0' }}>
        <div style={{ color: '#e2e8f0', fontWeight: 600 }}>{result.case_id}</div>
        <div style={{ color: '#64748b', fontSize: '12px' }}>{result.case_name}</div>
      </td>
      <td style={{ padding: '12px', color: '#cbd5e1' }}>{result.expected_decision ?? '—'}</td>
      <td style={{ padding: '12px', color: '#cbd5e1' }}>{result.actual_decision ?? '—'}</td>
      <td style={{ padding: '12px', color: '#cbd5e1', textAlign: 'right' }}>
        {result.expected_approved_amount != null ? fmtAmount(result.expected_approved_amount) : '—'}
      </td>
      <td style={{ padding: '12px', color: '#cbd5e1', textAlign: 'right' }}>
        {result.actual_approved_amount != null ? fmtAmount(result.actual_approved_amount) : '—'}
      </td>
      <td style={{ padding: '12px' }}>
        <ResultPill passed={result.passed} />
        {!result.passed && result.reasons.length > 0 && (
          <div style={{ color: '#fca5a5', fontSize: '11px', marginTop: '4px', maxWidth: '260px' }}>
            {result.reasons.join('; ')}
          </div>
        )}
      </td>
    </tr>
  )
}

export function Reports() {
  const { report, loading, error, refetch } = useEvaluation()

  return (
    <div style={{ padding: '40px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: '0 0 6px', fontSize: '24px', fontWeight: 700, color: '#f1f5f9' }}>
            Official Evaluation Report
          </h1>
          <p style={{ margin: 0, color: '#64748b', fontSize: '14px' }}>
            All 12 official test_cases.json cases, run live through the real pipeline.
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
          {loading ? '⟳ Running...' : '↻ Re-run Evaluation'}
        </button>
      </div>

      {loading && !report && (
        <div style={{ ...CARD, textAlign: 'center', padding: '40px', color: '#64748b' }}>
          <div style={{ fontSize: '28px', marginBottom: '8px' }}>⟳</div>
          Running all 12 official cases through the real pipeline...
        </div>
      )}

      {!loading && error && (
        <div
          data-testid="reports-error"
          style={{ ...CARD, background: 'rgba(239, 68, 68, 0.1)', borderColor: 'rgba(239, 68, 68, 0.3)' }}
        >
          <strong style={{ color: '#fca5a5' }}>⚠ Unable to load the evaluation report. Please try again.</strong>
          <p style={{ margin: '8px 0 0', fontSize: '13px', color: '#fca5a5', opacity: 0.8 }}>{error}</p>
        </div>
      )}

      {!loading && !error && report && (
        <>
          <div
            data-testid="eval-summary"
            style={{
              ...CARD,
              background: report.all_passed ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
              borderColor: report.all_passed ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)',
              borderWidth: '2px',
              display: 'flex',
              alignItems: 'center',
              gap: '36px',
            }}
          >
            <div>
              <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
                Official Evaluation
              </div>
              <div style={{ fontSize: '36px', fontWeight: 800, color: report.all_passed ? '#86efac' : '#fca5a5' }}>
                {report.passed} / {report.total}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
                Decision Accuracy
              </div>
              <div style={{ fontSize: '22px', fontWeight: 700, color: '#e2e8f0' }}>
                {Math.round((report.passed / report.total) * 100)}%
              </div>
            </div>
            <div style={{ marginLeft: 'auto', fontSize: '14px', fontWeight: 700, color: report.all_passed ? '#86efac' : '#fca5a5' }}>
              {report.all_passed ? '✓ All cases pass' : `${report.total - report.passed} case(s) failing`}
            </div>
          </div>

          <div style={CARD}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: '#64748b', fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    <th style={{ padding: '0 12px 12px 0', fontWeight: 600 }}>Case</th>
                    <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Expected Decision</th>
                    <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Actual Decision</th>
                    <th style={{ padding: '0 12px 12px', fontWeight: 600, textAlign: 'right' }}>Expected Amount</th>
                    <th style={{ padding: '0 12px 12px', fontWeight: 600, textAlign: 'right' }}>Actual Amount</th>
                    <th style={{ padding: '0 12px 12px', fontWeight: 600 }}>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {report.results.map(r => (
                    <CaseRow key={r.case_id} result={r} />
                  ))}
                </tbody>
              </table>
            </div>
            <p style={{ margin: '16px 0 0', fontSize: '11px', color: '#64748b' }}>
              TC001-TC003 stop before a decision by design (early document-problem
              detection) — "—" there means no decision was expected or produced,
              not a missing value.
            </p>
          </div>
        </>
      )}
    </div>
  )
}
