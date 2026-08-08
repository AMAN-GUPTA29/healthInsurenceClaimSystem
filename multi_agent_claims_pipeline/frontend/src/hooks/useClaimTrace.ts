/**
 * useClaimTrace hook — fetches trace events for a claim from the backend.
 *
 * Mirrors useHealth.ts's shape. Not wired into any page yet (no claim
 * submission flow exists until Phase 2) — this exists so the trace-viewer
 * foundation is ready to drop into a future claim detail page without
 * another round of data-fetching plumbing.
 */

import { useEffect, useState } from 'react'
import { traceApi } from '../services/api'
import type { TraceEvent } from '../types'

interface UseClaimTraceResult {
  events: TraceEvent[]
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useClaimTrace(claimId: string | null): UseClaimTraceResult {
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!claimId) {
      setEvents([])
      return
    }

    let cancelled = false

    const fetchTrace = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await traceApi.getClaimTrace(claimId)
        if (!cancelled) {
          setEvents(data.events)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load claim trace')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchTrace()
    return () => {
      cancelled = true
    }
  }, [claimId, tick])

  return {
    events,
    loading,
    error,
    refetch: () => setTick(t => t + 1),
  }
}
