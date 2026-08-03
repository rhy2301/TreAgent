import { useEffect, useMemo, useReducer, useState } from 'react'
import type { ScenarioName, Snapshot } from './types'
import { SCENARIOS } from './types'
import { useSnapshot } from './lib/useSnapshot'
import {
  currentSpec,
  initialRunState,
  reached,
  revealed,
  runReducer,
  STAGE_SEQUENCE,
} from './lib/stages'
import { displaySnapshot, runExecutionCycle } from './lib/execution'
import { Sidebar } from './components/Sidebar'
import { Header } from './components/Header'
import { KpiRow } from './components/KpiRow'
import { WalletSection } from './components/WalletSection'
import { InferenceCard } from './components/InferenceCard'
import { DecisionCard } from './components/DecisionCard'
import { FundFlow } from './components/FundFlow'
import { ComparisonPanel } from './components/ComparisonPanel'
import { TransactionsTable } from './components/TransactionsTable'

/**
 * Reveals guard checks one row at a time across the VALIDATING window, so the
 * policy layer reads as a sequence of independent checks rather than a block.
 */
function useStaggeredChecks(active: boolean, total: number, windowMs: number) {
  const [count, setCount] = useState(0)

  useEffect(() => {
    if (!active || total === 0) return
    setCount(0)
    const step = Math.max(60, Math.floor(windowMs / (total + 1)))
    const id = window.setInterval(() => {
      setCount((c) => {
        if (c >= total) {
          window.clearInterval(id)
          return c
        }
        return c + 1
      })
    }, step)
    return () => window.clearInterval(id)
  }, [active, total, windowMs])

  return count
}

/** `?scenario=prompt_injection` deep-links a scenario for the demo recording. */
function initialScenario(): ScenarioName {
  const q = new URLSearchParams(window.location.search).get('scenario')
  return (SCENARIOS as readonly string[]).includes(q ?? '')
    ? (q as ScenarioName)
    : 'normal_shortfall'
}

export function App() {
  const [scenario, setScenario] = useState<ScenarioName>(initialScenario)
  const { snap: baseSnap, error, loading } = useSnapshot(scenario)
  const [run, dispatch] = useReducer(runReducer, initialRunState)

  // `committed` is the persisted state a new run builds on; `pending` is the
  // next cycle's precomputed result, revealed field-by-field as stages pass
  // (see lib/execution.ts). This is what makes Run Agent actually change
  // wallet balances/decision id/x402 proof instead of replaying the same
  // static snapshot.json every time.
  const [committed, setCommitted] = useState<Snapshot | null>(null)
  const [pending, setPending] = useState<Snapshot | null>(null)

  useEffect(() => {
    setCommitted(baseSnap)
    setPending(null)
  }, [baseSnap])

  // Each stage holds for its scripted duration, then hands off to the next.
  useEffect(() => {
    const spec = currentSpec(run)
    if (!spec) return
    const id = window.setTimeout(() => dispatch({ type: 'ADVANCE' }), spec.ms)
    return () => window.clearTimeout(id)
  }, [run])

  useEffect(() => {
    dispatch({ type: 'RESET' })
  }, [scenario])

  // Cycle finished — fold the precomputed result into the persisted state so
  // the next Run Agent click builds on it (transactions/decisions accumulate).
  useEffect(() => {
    if (run.stage === 'DONE' && pending) setCommitted(pending)
  }, [run.stage, pending])

  const snap = useMemo(
    () => (committed ? displaySnapshot(committed, pending, run.stage) : null),
    [committed, pending, run.stage],
  )

  const handleRun = () => {
    if (!committed) return
    setPending(runExecutionCycle(committed))
    dispatch({ type: 'START' })
  }

  const handleReset = () => {
    setCommitted(baseSnap)
    setPending(null)
    dispatch({ type: 'RESET' })
  }

  const stage = run.stage
  const show = (target: Parameters<typeof reached>[1]) =>
    revealed(stage, target, run.hasRun)

  const maxChecks = snap
    ? Math.max(0, ...snap.guard.map((g) => g.checks.length))
    : 0
  const validatingMs = STAGE_SEQUENCE.find((s) => s.stage === 'VALIDATING')?.ms ?? 1400
  const staggered = useStaggeredChecks(stage === 'VALIDATING', maxChecks, validatingMs)
  // Before a run, and after VALIDATING, every check is shown at once.
  const checkCount =
    stage === 'VALIDATING' ? staggered : show('VALIDATING') ? maxChecks : 0

  return (
    <div className="flex h-full">
      <Sidebar snap={snap} />

      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        <Header
          snap={snap}
          run={run}
          scenario={scenario}
          onScenario={setScenario}
          onRun={handleRun}
          onReset={handleReset}
        />

        <main className="flex flex-col gap-4 px-8 py-5">
          {error && (
            <div className="rounded-[12px] border border-danger/45 bg-danger/10 px-4 py-3 text-[12px] text-danger">
              {error}
            </div>
          )}
          {loading && !snap && (
            <div className="text-[12px] text-muted">스냅샷 로딩 중…</div>
          )}

          {snap && (
            <>
              <KpiRow snap={snap} />

              <div className="grid grid-cols-3 gap-4">
                <div className="col-span-2">
                  <WalletSection snap={snap} showShortfall={show('FORECASTING')} />
                </div>
                <InferenceCard
                  snap={snap}
                  lit={show('PAYING')}
                  reasoning={run.hasRun && stage === 'REASONING'}
                  reasoningDone={!run.hasRun || reached(stage, 'VALIDATING')}
                />
              </div>

              <DecisionCard
                snap={snap}
                ready={show('VALIDATING')}
                checkCount={checkCount}
              />

              <div className="grid grid-cols-2 gap-4">
                <FundFlow
                  snap={snap}
                  executing={stage === 'EXECUTING'}
                  done={!run.hasRun || stage === 'DONE'}
                />
                <ComparisonPanel snap={snap} />
              </div>

              <TransactionsTable snap={snap} />
            </>
          )}
        </main>
      </div>
    </div>
  )
}
