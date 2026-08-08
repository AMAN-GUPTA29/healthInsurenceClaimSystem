/**
 * ClaimSubmission page — the first real claim submission experience.
 *
 * No file-upload/OCR pipeline exists yet (Phase 2A scope), so each
 * document row is entered manually: a simulated type, quality, and
 * patient name standing in for what real AI document classification
 * would produce. This is clearly labelled as such in the UI — it is not
 * presented as if it were real OCR. See backend
 * app/services/document_input_adapter.py for the same distinction on the
 * API side.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { claimsApi, APIClientError } from '../services/api'
import type {
  ClaimCategory,
  ClaimDocumentInput,
  DocumentQuality,
  DocumentType,
} from '../types'

// ── Style Helpers (matches Dashboard.tsx's existing dark theme) ──────────────

const CARD: React.CSSProperties = {
  background: 'rgba(30, 41, 59, 0.6)',
  border: '1px solid rgba(99, 102, 241, 0.2)',
  borderRadius: '16px',
  padding: '28px',
  backdropFilter: 'blur(8px)',
}

const LABEL: React.CSSProperties = {
  display: 'block',
  fontSize: '12px',
  color: '#94a3b8',
  marginBottom: '6px',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
}

const INPUT: React.CSSProperties = {
  width: '100%',
  background: 'rgba(15, 23, 42, 0.7)',
  border: '1px solid rgba(51, 65, 85, 0.8)',
  borderRadius: '8px',
  padding: '10px 12px',
  color: '#e2e8f0',
  fontSize: '14px',
  outline: 'none',
}

const CATEGORIES: ClaimCategory[] = [
  'CONSULTATION',
  'DIAGNOSTIC',
  'PHARMACY',
  'DENTAL',
  'VISION',
  'ALTERNATIVE_MEDICINE',
]

const DOCUMENT_TYPES: DocumentType[] = [
  'PRESCRIPTION',
  'HOSPITAL_BILL',
  'LAB_REPORT',
  'DIAGNOSTIC_REPORT',
  'PHARMACY_BILL',
  'DISCHARGE_SUMMARY',
  'DENTAL_REPORT',
  'PRE_AUTH_LETTER',
  'UNKNOWN',
]

const QUALITIES: DocumentQuality[] = ['GOOD', 'LOW_QUALITY', 'PARTIAL', 'UNREADABLE', 'UNKNOWN']

interface DocRow extends ClaimDocumentInput {
  key: string
}

let rowKeySeq = 0
function newRow(overrides: Partial<DocRow> = {}): DocRow {
  rowKeySeq += 1
  return {
    key: `row-${rowKeySeq}`,
    file_id: `F${String(rowKeySeq).padStart(3, '0')}`,
    file_name: '',
    actual_type: 'PRESCRIPTION',
    quality: 'GOOD',
    patient_name_on_doc: '',
    ...overrides,
  }
}

// ── Example Loaders (for demo / manual testing — see docs/AI_HANDOFF.md) ────

const EXAMPLES = {
  TC001: {
    label: 'TC001 — Wrong Document',
    member_id: 'EMP001',
    claim_category: 'CONSULTATION' as ClaimCategory,
    treatment_date: '2024-11-01',
    claimed_amount: 1500,
    documents: [
      newRow({ file_name: 'dr_sharma_prescription.jpg', actual_type: 'PRESCRIPTION' }),
      newRow({ file_name: 'another_prescription.jpg', actual_type: 'PRESCRIPTION' }),
    ],
  },
  TC002: {
    label: 'TC002 — Unreadable Document',
    member_id: 'EMP004',
    claim_category: 'PHARMACY' as ClaimCategory,
    treatment_date: '2024-10-25',
    claimed_amount: 800,
    documents: [
      newRow({ file_name: 'prescription.jpg', actual_type: 'PRESCRIPTION', quality: 'GOOD' }),
      newRow({ file_name: 'blurry_bill.jpg', actual_type: 'PHARMACY_BILL', quality: 'UNREADABLE' }),
    ],
  },
  TC003: {
    label: 'TC003 — Different Patients',
    member_id: 'EMP001',
    claim_category: 'CONSULTATION' as ClaimCategory,
    treatment_date: '2024-11-01',
    claimed_amount: 1500,
    documents: [
      newRow({ file_name: 'prescription_rajesh.jpg', actual_type: 'PRESCRIPTION', patient_name_on_doc: 'Rajesh Kumar' }),
      newRow({ file_name: 'bill_arjun.jpg', actual_type: 'HOSPITAL_BILL', patient_name_on_doc: 'Arjun Mehta' }),
    ],
  },
  CLEAN: {
    label: 'Clean example — clears Phase 2A',
    member_id: 'EMP001',
    claim_category: 'CONSULTATION' as ClaimCategory,
    treatment_date: '2024-11-01',
    claimed_amount: 1500,
    documents: [
      newRow({ file_name: 'prescription.jpg', actual_type: 'PRESCRIPTION', patient_name_on_doc: 'Rajesh Kumar' }),
      newRow({ file_name: 'hospital_bill.jpg', actual_type: 'HOSPITAL_BILL', patient_name_on_doc: 'Rajesh Kumar' }),
    ],
  },
} as const

export function ClaimSubmission() {
  const navigate = useNavigate()
  const [memberId, setMemberId] = useState('EMP001')
  const [policyId, setPolicyId] = useState('PLUM_GHI_2024')
  const [category, setCategory] = useState<ClaimCategory>('CONSULTATION')
  const [treatmentDate, setTreatmentDate] = useState('2024-11-01')
  const [claimedAmount, setClaimedAmount] = useState('1500')
  const [documents, setDocuments] = useState<DocRow[]>([
    newRow({ file_name: 'prescription.jpg' }),
    newRow({ file_name: 'hospital_bill.jpg', actual_type: 'HOSPITAL_BILL' }),
  ])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function loadExample(key: keyof typeof EXAMPLES) {
    const ex = EXAMPLES[key]
    setMemberId(ex.member_id)
    setCategory(ex.claim_category)
    setTreatmentDate(ex.treatment_date)
    setClaimedAmount(String(ex.claimed_amount))
    setDocuments(ex.documents.map(d => ({ ...d })))
  }

  function updateDoc(key: string, patch: Partial<DocRow>) {
    setDocuments(rows => rows.map(r => (r.key === key ? { ...r, ...patch } : r)))
  }

  function addDoc() {
    setDocuments(rows => [...rows, newRow()])
  }

  function removeDoc(key: string) {
    setDocuments(rows => rows.filter(r => r.key !== key))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const response = await claimsApi.submit({
        member_id: memberId,
        policy_id: policyId,
        claim_category: category,
        treatment_date: treatmentDate,
        claimed_amount: Number(claimedAmount),
        documents: documents.map(({ key, ...rest }) => ({
          ...rest,
          patient_name_on_doc: rest.patient_name_on_doc || undefined,
        })),
      })
      navigate(`/claims/${response.claim_id}`)
    } catch (err) {
      if (err instanceof APIClientError) {
        setError(err.apiError.message || 'Submission failed.')
      } else {
        setError(err instanceof Error ? err.message : 'Could not reach the backend.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ padding: '40px 0', maxWidth: '820px' }}>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ margin: '0 0 6px', fontSize: '24px', fontWeight: 700, color: '#f1f5f9' }}>
          Submit a Claim
        </h1>
        <p style={{ margin: 0, color: '#64748b', fontSize: '14px' }}>
          Phase 2A: claim validation, document verification, and cross-document
          validation. No policy decision is generated yet.
        </p>
      </div>

      <div style={{ display: 'flex', gap: '8px', marginBottom: '24px', flexWrap: 'wrap' }}>
        {(Object.keys(EXAMPLES) as (keyof typeof EXAMPLES)[]).map(key => (
          <button
            key={key}
            type="button"
            onClick={() => loadExample(key)}
            style={{
              background: 'rgba(99, 102, 241, 0.12)',
              border: '1px solid rgba(99, 102, 241, 0.3)',
              borderRadius: '8px',
              color: '#a5b4fc',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {EXAMPLES[key].label}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit}>
        <div style={{ ...CARD, marginBottom: '20px' }}>
          <h2 style={{ margin: '0 0 20px', fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>
            Claim Details
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '16px' }}>
            <div>
              <label style={LABEL}>Member ID</label>
              <input style={INPUT} value={memberId} onChange={e => setMemberId(e.target.value)} required />
            </div>
            <div>
              <label style={LABEL}>Policy ID</label>
              <input style={INPUT} value={policyId} onChange={e => setPolicyId(e.target.value)} required />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
            <div>
              <label style={LABEL}>Claim Category</label>
              <select style={INPUT} value={category} onChange={e => setCategory(e.target.value as ClaimCategory)}>
                {CATEGORIES.map(c => (
                  <option key={c} value={c}>
                    {c.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label style={LABEL}>Treatment Date</label>
              <input
                type="date"
                style={INPUT}
                value={treatmentDate}
                onChange={e => setTreatmentDate(e.target.value)}
                required
              />
            </div>
            <div>
              <label style={LABEL}>Claimed Amount (₹)</label>
              <input
                type="number"
                style={INPUT}
                value={claimedAmount}
                onChange={e => setClaimedAmount(e.target.value)}
                min="0"
                step="1"
                required
              />
            </div>
          </div>
        </div>

        <div style={{ ...CARD, marginBottom: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <h2 style={{ margin: 0, fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>
              Documents
            </h2>
            <button
              type="button"
              onClick={addDoc}
              style={{
                background: 'rgba(34, 197, 94, 0.12)',
                border: '1px solid rgba(34, 197, 94, 0.3)',
                borderRadius: '8px',
                color: '#86efac',
                padding: '5px 12px',
                fontSize: '12px',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              + Add document
            </button>
          </div>
          <p style={{ margin: '0 0 16px', fontSize: '12px', color: '#64748b' }}>
            No file upload yet — each row simulates what AI document
            classification would find (type, quality, patient name). A real
            submission would only need a file; the backend calls the
            configured AI provider to classify it.
          </p>

          {documents.map((doc, idx) => (
            <div
              key={doc.key}
              style={{
                background: 'rgba(15, 23, 42, 0.5)',
                border: '1px solid rgba(51, 65, 85, 0.6)',
                borderRadius: '10px',
                padding: '14px',
                marginBottom: '10px',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '10px' }}>
                <span style={{ fontSize: '12px', color: '#64748b', fontWeight: 600 }}>
                  Document {idx + 1}
                </span>
                {documents.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeDoc(doc.key)}
                    style={{ background: 'none', border: 'none', color: '#fca5a5', cursor: 'pointer', fontSize: '12px' }}
                  >
                    Remove
                  </button>
                )}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '10px' }}>
                <div>
                  <label style={LABEL}>File name</label>
                  <input
                    style={INPUT}
                    value={doc.file_name ?? ''}
                    onChange={e => updateDoc(doc.key, { file_name: e.target.value })}
                    placeholder="prescription.jpg"
                  />
                </div>
                <div>
                  <label style={LABEL}>Simulated type</label>
                  <select
                    style={INPUT}
                    value={doc.actual_type}
                    onChange={e => updateDoc(doc.key, { actual_type: e.target.value as DocumentType })}
                  >
                    {DOCUMENT_TYPES.map(t => (
                      <option key={t} value={t}>
                        {t.replace(/_/g, ' ')}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label style={LABEL}>Quality</label>
                  <select
                    style={INPUT}
                    value={doc.quality}
                    onChange={e => updateDoc(doc.key, { quality: e.target.value as DocumentQuality })}
                  >
                    {QUALITIES.map(q => (
                      <option key={q} value={q}>
                        {q.replace(/_/g, ' ')}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label style={LABEL}>Patient name (optional)</label>
                  <input
                    style={INPUT}
                    value={doc.patient_name_on_doc ?? ''}
                    onChange={e => updateDoc(doc.key, { patient_name_on_doc: e.target.value })}
                    placeholder="Rajesh Kumar"
                  />
                </div>
              </div>
            </div>
          ))}
        </div>

        {error && (
          <div
            style={{
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              borderRadius: '10px',
              padding: '14px',
              color: '#fca5a5',
              marginBottom: '20px',
              fontSize: '13px',
            }}
          >
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          style={{
            background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)',
            border: 'none',
            borderRadius: '10px',
            color: 'white',
            padding: '12px 28px',
            fontSize: '14px',
            fontWeight: 600,
            cursor: submitting ? 'not-allowed' : 'pointer',
            opacity: submitting ? 0.6 : 1,
            boxShadow: '0 8px 24px rgba(99, 102, 241, 0.3)',
          }}
        >
          {submitting ? 'Submitting…' : 'Submit Claim'}
        </button>
      </form>
    </div>
  )
}
