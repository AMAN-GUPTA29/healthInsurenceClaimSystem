/**
 * useClaims hook — fetches the real, persisted claim list from the
 * backend (Claim History). Same shape/pattern as useHealth.ts.
 */

import { useEffect, useState } from 'react'
import { claimsApi, APIClientError } from '../services/api'
import type { ClaimSummary } from '../types'

interface UseClaimsResult {
  claims: ClaimSummary[]
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useClaims(limit = 100): UseClaimsResult {
  const [claims, setClaims] = useState<ClaimSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false

    const fetchClaims = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await claimsApi.list(limit)
        if (!cancelled) {
          setClaims(data.claims)
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof APIClientError
              ? err.apiError.message
              : 'Unable to load claims. Please try again.'
          )
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchClaims()
    return () => {
      cancelled = true
    }
  }, [limit, tick])

  return {
    claims,
    loading,
    error,
    refetch: () => setTick(t => t + 1),
  }
}
