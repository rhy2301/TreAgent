import { useCallback, useEffect, useState } from 'react'
import type { ScenarioName, Snapshot } from '../types'

/**
 * The dashboard's only data source. Nothing is hardcoded: every figure on
 * screen comes from `public/snapshot.<scenario>.json`, written by
 * `scripts/demo.py --scenario <name> --emit-json`.
 *
 * `snapshot.json` (no suffix) is honoured first when present, so a plain
 * `--emit-json frontend/public/snapshot.json` run also lights up the UI.
 */
export function useSnapshot(scenario: ScenarioName) {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (name: ScenarioName, signal: AbortSignal) => {
    setLoading(true)
    setError(null)
    const candidates = [`/snapshot.${name}.json`, '/snapshot.json']
    for (const url of candidates) {
      try {
        const res = await fetch(url, { signal, cache: 'no-store' })
        if (!res.ok) continue
        const data = (await res.json()) as Snapshot
        if (data.scenario && data.scenario !== name && url === '/snapshot.json') continue
        setSnap(data)
        setLoading(false)
        return
      } catch (e) {
        if ((e as Error).name === 'AbortError') return
      }
    }
    setError(`스냅샷을 찾을 수 없습니다: snapshot.${name}.json`)
    setLoading(false)
  }, [])

  useEffect(() => {
    const ctrl = new AbortController()
    void load(scenario, ctrl.signal)
    return () => ctrl.abort()
  }, [scenario, load])

  return { snap, error, loading }
}
