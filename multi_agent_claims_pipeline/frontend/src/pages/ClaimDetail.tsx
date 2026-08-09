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
import type { ClaimResponse, ClaimStatus, DocumentExtractionResult } from '../types'

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

// A document's processing_status stays PENDING forever once the pipeline
// stops at CLAIM_VALIDATION — DocumentVerificationAgent never ran, so
// PENDING here does not mean "in progress," it means "never reached."
// Derived entirely from real backend fields (claim.stopped_at,
// doc.processing_status), not a frontend-invented state.
const DOCUMENT_VERIFICATION_SKIP_REASON: Record<string, string> = {
  CLAIM_VALIDATION: 'Document verification was skipped because claim validation failed.',
}

function fmtAmount(amount?: number): string {
  if (amount == null) return '—'
  return `₹${amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function Field({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div style={{ marginBottom: '6px' }}>
      <span style={{ color: '#64748b' }}>{label}: </span>
      <span style={{ color: '#e2e8f0' }}>{value}</span>
    </div>
  )
}

function ExtractionSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '14px' }}>
      <div style={{ fontSize: '11px', color: '#818cf8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '6px', fontWeight: 700 }}>
        {title}
      </div>
      <div style={{ fontSize: '13px' }}>{children}</div>
    </div>
  )
}

/** Document-type-specific rendering of one extraction payload — an
 * operations-friendly view, never raw JSON as the primary UI. */
function ExtractedInfo({ result }: { result: DocumentExtractionResult }) {
  const e = result.extraction

  return (
    <div
      data-testid="extracted-information"
      style={{ borderTop: '1px solid rgba(51, 65, 85, 0.6)', marginTop: '12px', paddingTop: '12px' }}
    >
      {e.warnings.length > 0 && (
        <div
          style={{
            background: 'rgba(245, 158, 11, 0.1)',
            border: '1px solid rgba(245, 158, 11, 0.3)',
            borderRadius: '6px',
            padding: '8px 10px',
            marginBottom: '12px',
          }}
        >
          {e.warnings.map((w, i) => (
            <div key={i} data-testid="extraction-warning" style={{ fontSize: '12px', color: '#fcd34d' }}>
              ⚠ {w}
            </div>
          ))}
        </div>
      )}

      {e.document_type === 'PRESCRIPTION' && (
        <>
          <ExtractionSection title="Doctor">
            <Field label="Name" value={e.doctor.name} />
            <Field label="Registration" value={e.doctor.registration_number} />
            <Field label="Clinic" value={e.doctor.hospital_or_clinic} />
          </ExtractionSection>
          <ExtractionSection title="Diagnosis">
            <Field label="" value={e.diagnosis} />
          </ExtractionSection>
          <Field label="Date" value={e.prescription_date} />
          {e.medications.length > 0 && (
            <ExtractionSection title="Medications">
              {e.medications.map((m, i) => (
                <div key={i} style={{ marginBottom: '8px' }}>
                  <div style={{ color: '#e2e8f0' }}>
                    {m.name}
                    {m.strength ? ` ${m.strength}` : ''}
                  </div>
                  <div style={{ color: '#94a3b8', fontSize: '12px' }}>
                    {[m.dosage, m.frequency, m.duration].filter(Boolean).join(' · ') || '—'}
                  </div>
                </div>
              ))}
            </ExtractionSection>
          )}
          {e.investigations.length > 0 && (
            <ExtractionSection title="Investigations">{e.investigations.join(', ')}</ExtractionSection>
          )}
        </>
      )}

      {e.document_type === 'HOSPITAL_BILL' && (
        <>
          <Field label="Hospital" value={e.hospital_name} />
          <Field label="Bill Number" value={e.bill_number} />
          <Field label="Date" value={e.bill_date} />
          <Field label="Patient" value={e.patient_name} />
          {e.line_items.length > 0 && (
            <ExtractionSection title="Line Items">
              {e.line_items.map((li, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', color: '#cbd5e1' }}>
                  <span>{li.description}</span>
                  <span>{fmtAmount(li.amount)}</span>
                </div>
              ))}
            </ExtractionSection>
          )}
          <div style={{ marginTop: '8px', fontWeight: 700, color: '#e2e8f0', display: 'flex', justifyContent: 'space-between' }}>
            <span>Total</span>
            <span>{fmtAmount(e.total)}</span>
          </div>
        </>
      )}

      {e.document_type === 'LAB_REPORT' && (
        <>
          <Field label="Laboratory" value={e.laboratory_name} />
          <Field label="Referring Doctor" value={e.referring_doctor} />
          <Field label="Sample Date" value={e.sample_date} />
          <Field label="Report Date" value={e.report_date} />
          {e.tests.length > 0 && (
            <ExtractionSection title="Tests">
              {e.tests.map((t, i) => (
                <div key={i} style={{ marginBottom: '6px', color: '#cbd5e1' }}>
                  <span style={{ color: '#e2e8f0' }}>{t.test_name}</span>
                  {t.result && <span> — {t.result}{t.unit ? ` ${t.unit}` : ''}</span>}
                  {t.reference_range && <span style={{ color: '#64748b' }}> (Ref: {t.reference_range})</span>}
                  {t.abnormal_flag && <span style={{ color: '#fca5a5' }}> ⚠ Abnormal</span>}
                </div>
              ))}
            </ExtractionSection>
          )}
        </>
      )}

      {e.document_type === 'PHARMACY_BILL' && (
        <>
          <Field label="Pharmacy" value={e.pharmacy_name} />
          <Field label="Bill Number" value={e.bill_number} />
          <Field label="Date" value={e.bill_date} />
          <Field label="Patient" value={e.patient_name} />
          {e.items.length > 0 && (
            <ExtractionSection title="Medicines">
              {e.items.map((it, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', color: '#cbd5e1' }}>
                  <span>
                    {it.medicine_name}
                    {it.quantity != null ? ` × ${it.quantity}` : ''}
                  </span>
                  <span>{fmtAmount(it.amount)}</span>
                </div>
              ))}
            </ExtractionSection>
          )}
          <div style={{ marginTop: '8px', fontWeight: 700, color: '#e2e8f0', display: 'flex', justifyContent: 'space-between' }}>
            <span>Total</span>
            <span>{fmtAmount(e.total)}</span>
          </div>
        </>
      )}

      {e.document_type === 'DENTAL_REPORT' && (
        <>
          <Field label="Dentist" value={e.dentist.name} />
          <Field label="Clinic" value={e.dentist.hospital_or_clinic} />
          <Field label="Date" value={e.date_on_document} />
          <Field label="Diagnosis" value={e.diagnosis} />
          <Field label="Procedure" value={e.procedure} />
          <div style={{ marginTop: '8px', fontWeight: 700, color: '#e2e8f0', display: 'flex', justifyContent: 'space-between' }}>
            <span>Amount</span>
            <span>{fmtAmount(e.amount)}</span>
          </div>
        </>
      )}

      {e.document_type === 'DISCHARGE_SUMMARY' && (
        <>
          <Field label="Hospital" value={e.hospital_name} />
          <Field label="Admission" value={e.admission_date} />
          <Field label="Discharge" value={e.discharge_date} />
          <Field label="Diagnosis" value={e.diagnosis} />
          <Field label="Procedure / Treatment" value={e.procedure_or_treatment} />
          <Field label="Doctor" value={e.doctor.name} />
          <Field label="Instructions" value={e.discharge_instructions} />
        </>
      )}

      <div style={{ marginTop: '10px', fontSize: '11px', color: '#64748b' }}>
        Extraction confidence: {e.confidence.toFixed(2)}
      </div>
    </div>
  )
}

function DocumentCard({
  doc,
  stoppedAt,
  extractionFailureReason,
}: {
  doc: ClaimResponse['documents'][number]
  stoppedAt?: string
  extractionFailureReason?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const skipReason =
    doc.processing_status === 'PENDING' && stoppedAt
      ? DOCUMENT_VERIFICATION_SKIP_REASON[stoppedAt]
      : undefined
  const statusLabel = skipReason
    ? '○ Not processed'
    : PROCESSING_STATUS_LABEL[doc.processing_status] ?? doc.processing_status

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
        <span style={{ fontSize: '11px', color: '#64748b' }}>{statusLabel}</span>
      </div>
      {skipReason && (
        <p
          data-testid="document-skip-reason"
          style={{ margin: '0 0 8px', fontSize: '11px', color: '#94a3b8', lineHeight: 1.5 }}
        >
          {skipReason}
        </p>
      )}
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

      {doc.extraction && (
        <div style={{ marginTop: '10px' }}>
          <button
            type="button"
            data-testid="toggle-extracted-information"
            onClick={() => setExpanded(v => !v)}
            style={{
              background: 'none',
              border: 'none',
              color: '#a5b4fc',
              fontSize: '12px',
              fontWeight: 600,
              cursor: 'pointer',
              padding: 0,
            }}
          >
            {expanded ? '▾' : '▸'} Extracted Information
          </button>
          {expanded && <ExtractedInfo result={doc.extraction} />}
        </div>
      )}

      {!doc.extraction && extractionFailureReason && (
        <p
          data-testid="extraction-failure-reason"
          style={{ margin: '10px 0 0', fontSize: '11px', color: '#fca5a5', lineHeight: 1.5 }}
        >
          ⚠ Extraction failed for this document: {extractionFailureReason}
        </p>
      )}
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
  const extractionFailuresByFileId = new Map(
    (claim.extraction_result?.failures ?? []).map(f => [f.file_id, f.reason])
  )
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
            <DocumentCard
              key={doc.file_id}
              doc={doc}
              stoppedAt={claim.stopped_at}
              extractionFailureReason={extractionFailuresByFileId.get(doc.file_id)}
            />
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
