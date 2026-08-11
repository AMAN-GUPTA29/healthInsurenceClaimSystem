/**
 * Tests for ClaimSubmission — the real file-upload UI (Phase 2A correction).
 * Verifies the file picker, multi-file selection, removal, and that
 * submission sends real File objects via claimsApi.submit (multipart).
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import { ClaimSubmission } from './ClaimSubmission'
import { claimsApi } from '../services/api'

vi.mock('../services/api', () => ({
  claimsApi: { submit: vi.fn() },
  APIClientError: class APIClientError extends Error {
    apiError: { message: string }
    constructor(apiError: { message: string }) {
      super(apiError.message)
      this.apiError = apiError
    }
  },
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

function renderPage() {
  return render(
    <MemoryRouter>
      <ClaimSubmission />
    </MemoryRouter>
  )
}

function makeFile(name: string, type: string, sizeBytes = 1024): File {
  const content = new Uint8Array(sizeBytes)
  return new File([content], name, { type })
}

describe('ClaimSubmission', () => {
  beforeEach(() => {
    vi.mocked(claimsApi.submit).mockReset()
    mockNavigate.mockReset()
  })

  it('renders the empty documents state initially', () => {
    renderPage()
    expect(screen.getByText('No documents added yet.')).toBeInTheDocument()
  })

  it('has a hidden file input accepting pdf/jpg/jpeg/png', () => {
    renderPage()
    const input = screen.getByTestId('file-input') as HTMLInputElement
    expect(input.accept).toBe('.pdf,.jpg,.jpeg,.png')
    expect(input.multiple).toBe(true)
  })

  it('adds a selected file to the list', async () => {
    renderPage()
    const input = screen.getByTestId('file-input') as HTMLInputElement
    const file = makeFile('prescription.jpg', 'image/jpeg')

    await userEvent.upload(input, file)

    expect(screen.getByText('prescription.jpg')).toBeInTheDocument()
    expect(screen.queryByText('No documents added yet.')).not.toBeInTheDocument()
  })

  it('supports selecting multiple files at once', async () => {
    renderPage()
    const input = screen.getByTestId('file-input') as HTMLInputElement
    const rx = makeFile('rx.jpg', 'image/jpeg')
    const bill = makeFile('bill.pdf', 'application/pdf')

    await userEvent.upload(input, [rx, bill])

    expect(screen.getByText('rx.jpg')).toBeInTheDocument()
    expect(screen.getByText('bill.pdf')).toBeInTheDocument()
    expect(screen.getAllByTestId('selected-file-row')).toHaveLength(2)
  })

  it('removes a file when Remove is clicked', async () => {
    renderPage()
    const input = screen.getByTestId('file-input') as HTMLInputElement
    await userEvent.upload(input, makeFile('rx.jpg', 'image/jpeg'))
    expect(screen.getByText('rx.jpg')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /remove rx\.jpg/i }))

    expect(screen.queryByText('rx.jpg')).not.toBeInTheDocument()
    expect(screen.getByText('No documents added yet.')).toBeInTheDocument()
  })

  it('rejects a file with an unsupported type', async () => {
    // A real browser's file picker restricts by the `accept` attribute, and so
    // does @testing-library/user-event's upload() — it won't "select" a file
    // that doesn't match. But a user can still bypass that (drag-and-drop, or
    // choosing "All Files" in the OS dialog), so the component must defend
    // itself too. We simulate that bypass with a raw change event.
    renderPage()
    const input = screen.getByTestId('file-input') as HTMLInputElement
    const file = makeFile('malware.exe', 'application/x-msdownload')
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    fireEvent.change(input)

    expect(await screen.findByText(/is not a PDF, JPEG, or PNG file/)).toBeInTheDocument()
    expect(screen.queryByTestId('selected-file-row')).not.toBeInTheDocument()
  })

  it('blocks submission with no documents attached', async () => {
    renderPage()
    await userEvent.click(screen.getByRole('button', { name: /submit claim/i }))

    expect(screen.getByText(/add at least one document/i)).toBeInTheDocument()
    expect(claimsApi.submit).not.toHaveBeenCalled()
  })

  it('submits claim metadata and real files via claimsApi.submit', async () => {
    vi.mocked(claimsApi.submit).mockResolvedValue({
      claim_id: 'CLM-TEST123',
    } as never)

    renderPage()
    const input = screen.getByTestId('file-input') as HTMLInputElement
    const file = makeFile('rx.jpg', 'image/jpeg')
    await userEvent.upload(input, file)

    await userEvent.click(screen.getByRole('button', { name: /submit claim/i }))

    await waitFor(() => expect(claimsApi.submit).toHaveBeenCalledTimes(1))
    const [fields, files] = vi.mocked(claimsApi.submit).mock.calls[0]
    expect(fields.member_id).toBe('EMP001')
    expect(fields.claim_category).toBe('CONSULTATION')
    expect(files).toHaveLength(1)
    expect(files[0]).toBeInstanceOf(File)
    expect(files[0].name).toBe('rx.jpg')
  })

  it('navigates to the claim detail page after successful submission', async () => {
    vi.mocked(claimsApi.submit).mockResolvedValue({ claim_id: 'CLM-ABC999' } as never)

    renderPage()
    await userEvent.upload(screen.getByTestId('file-input'), makeFile('rx.jpg', 'image/jpeg'))
    await userEvent.click(screen.getByRole('button', { name: /submit claim/i }))

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/claims/CLM-ABC999'))
  })

  it('shows a server error message without navigating on failure', async () => {
    const { APIClientError } = await import('../services/api')
    vi.mocked(claimsApi.submit).mockRejectedValue(
      new APIClientError({ error: 'DOCUMENT_UNREADABLE', message: 'Document is unreadable.' }, 422)
    )

    renderPage()
    await userEvent.upload(screen.getByTestId('file-input'), makeFile('rx.jpg', 'image/jpeg'))
    await userEvent.click(screen.getByRole('button', { name: /submit claim/i }))

    await waitFor(() => expect(screen.getByText('Document is unreadable.')).toBeInTheDocument())
    expect(mockNavigate).not.toHaveBeenCalled()
  })

})
