/**
 * ClaimSubmission page — real document upload.
 *
 * The member never selects a document type — only the AI's read of the
 * actual uploaded file determines that (see backend
 * app/agents/document_verification_agent.py). This page's only job is to
 * collect claim metadata and real PDF/JPEG/PNG files and submit them as
 * multipart/form-data. Submission runs the claim through the complete
 * pipeline synchronously — the response already includes a final
 * decision (or a specific early-stop reason) by the time it comes back.
 */

import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { claimsApi, APIClientError } from '../services/api'
import type { ClaimCategory } from '../types'

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

const ACCEPTED_EXTENSIONS = '.pdf,.jpg,.jpeg,.png'
const ACCEPTED_MIME_TYPES = ['application/pdf', 'image/jpeg', 'image/jpg', 'image/png']
const MAX_FILE_BYTES = 10 * 1024 * 1024 // 10 MB — matches backend MAX_UPLOAD_BYTES

interface SelectedFile {
  key: string
  file: File
}

let keySeq = 0
function wrapFile(file: File): SelectedFile {
  keySeq += 1
  return { key: `file-${keySeq}-${file.name}`, file }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function ClaimSubmission() {
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [memberId, setMemberId] = useState('EMP001')
  const [policyId, setPolicyId] = useState('PLUM_GHI_2024')
  const [category, setCategory] = useState<ClaimCategory>('CONSULTATION')
  const [treatmentDate, setTreatmentDate] = useState('2024-11-01')
  const [claimedAmount, setClaimedAmount] = useState('1500')
  const [files, setFiles] = useState<SelectedFile[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleFilesSelected(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return
    setFileError(null)
    const accepted: SelectedFile[] = []
    for (const file of Array.from(fileList)) {
      if (!ACCEPTED_MIME_TYPES.includes(file.type)) {
        setFileError(`"${file.name}" is not a PDF, JPEG, or PNG file.`)
        continue
      }
      if (file.size > MAX_FILE_BYTES) {
        setFileError(`"${file.name}" is larger than 10 MB.`)
        continue
      }
      if (file.size === 0) {
        setFileError(`"${file.name}" is empty.`)
        continue
      }
      accepted.push(wrapFile(file))
    }
    setFiles(prev => [...prev, ...accepted])
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  function removeFile(key: string) {
    setFiles(prev => prev.filter(f => f.key !== key))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (files.length === 0) {
      setError('Please add at least one document before submitting.')
      return
    }

    setSubmitting(true)
    try {
      const response = await claimsApi.submit(
        {
          member_id: memberId,
          policy_id: policyId,
          claim_category: category,
          treatment_date: treatmentDate,
          claimed_amount: Number(claimedAmount),
        },
        files.map(f => f.file)
      )
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
    <div style={{ padding: '40px 0' }}>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ margin: '0 0 6px', fontSize: '24px', fontWeight: 700, color: '#f1f5f9' }}>
          Submit a Claim
        </h1>
        <p style={{ margin: 0, color: '#64748b', fontSize: '14px' }}>
          Upload real PDF, JPEG, or PNG documents. The AI determines each
          document's type, quality, and patient identity from its actual
          content, and the claim runs through the full pipeline —
          policy evaluation, financial calculation, fraud analysis, and a
          final decision — before you're taken to its detail page.
        </p>
      </div>

      <form onSubmit={handleSubmit}>
        <div style={{ ...CARD, marginBottom: '20px' }}>
          <h2 style={{ margin: '0 0 20px', fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>
            Claim Details
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px', marginBottom: '16px' }}>
            <div>
              <label style={LABEL}>Member ID</label>
              <input style={INPUT} value={memberId} onChange={e => setMemberId(e.target.value)} required />
            </div>
            <div>
              <label style={LABEL}>Policy ID</label>
              <input style={INPUT} value={policyId} onChange={e => setPolicyId(e.target.value)} required />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
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
              onClick={() => fileInputRef.current?.click()}
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
              + Add Document
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_EXTENSIONS}
              multiple
              data-testid="file-input"
              style={{ display: 'none' }}
              onChange={e => handleFilesSelected(e.target.files)}
            />
          </div>
          <p style={{ margin: '0 0 16px', fontSize: '12px', color: '#64748b' }}>
            PDF, JPG, JPEG, or PNG — up to 10 MB each. The AI reads the actual
            document content to determine its type; you don't need to (and
            can't) declare it yourself.
          </p>

          {files.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                padding: '28px',
                fontSize: '13px',
                color: '#64748b',
                border: '1px dashed rgba(51, 65, 85, 0.8)',
                borderRadius: '10px',
              }}
            >
              No documents added yet.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {files.map(({ key, file }) => (
                <div
                  key={key}
                  data-testid="selected-file-row"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    background: 'rgba(15, 23, 42, 0.5)',
                    border: '1px solid rgba(51, 65, 85, 0.6)',
                    borderRadius: '10px',
                    padding: '10px 14px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                    <span style={{ fontSize: '16px' }}>
                      {file.type === 'application/pdf' ? '📄' : '🖼️'}
                    </span>
                    <span
                      style={{
                        fontSize: '13px',
                        color: '#e2e8f0',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {file.name}
                    </span>
                    <span style={{ fontSize: '12px', color: '#64748b', flexShrink: 0 }}>
                      {formatFileSize(file.size)}
                    </span>
                    <span style={{ color: '#86efac', fontSize: '13px', flexShrink: 0 }}>✓</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeFile(key)}
                    aria-label={`Remove ${file.name}`}
                    style={{ background: 'none', border: 'none', color: '#fca5a5', cursor: 'pointer', fontSize: '12px', flexShrink: 0 }}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}

          {fileError && (
            <p style={{ margin: '10px 0 0', fontSize: '12px', color: '#fca5a5' }}>{fileError}</p>
          )}
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
