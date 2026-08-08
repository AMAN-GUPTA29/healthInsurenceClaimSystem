/**
 * useHealth hook — fetches system health status from the backend.
 */

import { useEffect, useState } from 'react'
import { systemApi } from '../services/api'
import type { HealthResponse } from '../types'

interface UseHealthResult {
  health: HealthResponse | null
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useHealth(): UseHealthResult {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false

    const fetchHealth = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await systemApi.health()
        if (!cancelled) {
          setHealth(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to connect to backend')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchHealth()
    return () => {
      cancelled = true
    }
  }, [tick])

  return {
    health,
    loading,
    error,
    refetch: () => setTick(t => t + 1),
  }
}
