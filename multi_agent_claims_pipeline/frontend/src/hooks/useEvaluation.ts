/**
 * useEvaluation hook — fetches the official 12-case evaluation report
 * from the backend (GET /api/v1/evaluation). Same shape/pattern as
 * useHealth.ts/useClaims.ts. Not polled automatically — the report is
 * only re-run when the caller explicitly refetches, since each call
 * re-executes all 12 cases through the real pipeline server-side.
 */

import { useEffect, useState } from 'react'
import { evaluationApi, APIClientError } from '../services/api'
import type { EvaluationReportResponse } from '../types'

interface UseEvaluationResult {
  report: EvaluationReportResponse | null
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useEvaluation(): UseEvaluationResult {
  const [report, setReport] = useState<EvaluationReportResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false

    const fetchReport = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await evaluationApi.getReport()
        if (!cancelled) {
          setReport(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof APIClientError
              ? err.apiError.message
              : 'Unable to load the evaluation report. Please try again.'
          )
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchReport()
    return () => {
      cancelled = true
    }
  }, [tick])

  return {
    report,
    loading,
    error,
    refetch: () => setTick(t => t + 1),
  }
}
