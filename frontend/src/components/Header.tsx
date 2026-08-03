import type { ScenarioName, Snapshot } from '../types'
import { SCENARIOS } from '../types'
import { clockTime } from '../lib/format'
import { currentSpec, isRunning, type RunState } from '../lib/stages'
import { StatusDot } from './ui'

/**
 * No network badge here on purpose: a single "Solana Devnet" chip in the header
 * would imply the x402 inference payment settles on Devnet too. Network scope
 * belongs to the sidebar and to each card.
 */
export function Header({
  snap,
  run,
  scenario,
  onScenario,
  onRun,
}: {
  snap: Snapshot | null
  run: RunState
  scenario: ScenarioName
  onScenario: (s: ScenarioName) => void
  onRun: () => void
}) {
  const spec = currentSpec(run)
  const running = isRunning(run.stage)
  const active = snap?.agent_status !== 'PAUSED'

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/92 backdrop-blur-md">
      <div className="flex items-center justify-between gap-6 px-8 py-4">
        <div>
          <h1 className="text-[19px] leading-tight font-semibold tracking-tight text-txt">
            Treasury Overview
          </h1>
          <p className="mt-0.5 text-[11px] text-muted">
            Last synchronized{' '}
            <span className="mono text-txt-2">{clockTime(snap?.generated_at)}</span>
          </p>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 rounded-[10px] border border-border bg-card px-2.5 py-[7px]">
            <span className="text-[10px] tracking-[0.1em] text-muted uppercase">
              Scenario
            </span>
            <select
              value={scenario}
              disabled={running}
              onChange={(e) => onScenario(e.target.value as ScenarioName)}
              className="mono cursor-pointer bg-transparent text-[12px] text-txt outline-none disabled:cursor-not-allowed disabled:opacity-50"
            >
              {SCENARIOS.map((s) => (
                <option key={s} value={s} className="bg-elevated text-txt">
                  {s}
                </option>
              ))}
            </select>
          </label>

          <span className="flex items-center gap-2 rounded-[10px] border border-border bg-card px-3 py-[7px] text-[12px] text-txt-2">
            <StatusDot tone={active ? 'mint' : 'muted'} pulse={active} />
            Agent {active ? 'Active' : 'Paused'}
          </span>

          <button
            type="button"
            onClick={onRun}
            disabled={running}
            className="rounded-[10px] bg-gradient-to-r from-mint to-mint-dark px-4 py-[9px] text-[12.5px] font-semibold text-[#05261C] shadow-[0_8px_22px_-12px_rgba(67,230,176,0.95)] transition-all duration-150 hover:brightness-110 disabled:cursor-wait disabled:opacity-60"
          >
            {running ? 'Running…' : 'Run Agent'}
          </button>

          <button
            type="button"
            aria-disabled
            className="cursor-not-allowed rounded-[10px] border border-border px-4 py-[9px] text-[12.5px] text-txt-2 opacity-60"
          >
            Pause Agent
          </button>
        </div>
      </div>

      {/* Thin progress rail + current stage label. */}
      <div className="relative h-[26px] border-t border-border/60 bg-bg-2/60">
        <div className="absolute inset-x-0 top-0 h-[2px] bg-elevated">
          <div
            className="h-full bg-gradient-to-r from-mint-dark to-mint transition-[width] duration-500 ease-out"
            style={{ width: `${run.progress}%` }}
          />
        </div>
        <div className="flex h-full items-center gap-3 px-8">
          {spec ? (
            <>
              <span className="mono text-[10px] tracking-[0.14em] text-mint">
                {spec.label}
              </span>
              <span className="text-[11px] text-txt-2">{spec.caption}</span>
              <span className="mono ml-auto text-[10px] text-muted">
                {run.progress}%
              </span>
            </>
          ) : (
            <span className="mono text-[10px] tracking-[0.14em] text-muted">
              {run.stage === 'DONE' ? 'CYCLE COMPLETE' : 'IDLE'}
              {snap && (
                <span className="ml-3 text-muted normal-case">
                  decision {snap.decision_id}
                </span>
              )}
            </span>
          )}
        </div>
      </div>
    </header>
  )
}
