/**
 * Run Agent stage machine.
 *
 * Each stage lights up a specific card, so the demo reads as a pipeline rather
 * than a single result popping into place. REASONING is deliberately the
 * longest leg — real inference takes 5.9–6.6s, and a fast fake looks fake.
 */

export const STAGES = [
  'IDLE',
  'FORECASTING',
  'PAYING',
  'REASONING',
  'VALIDATING',
  'EXECUTING',
  'DONE',
] as const

export type Stage = (typeof STAGES)[number]

export type RunStage = Exclude<Stage, 'IDLE' | 'DONE'>

const RUN_STAGES: RunStage[] = ['FORECASTING', 'PAYING', 'REASONING', 'VALIDATING', 'EXECUTING']

export interface StageSpec {
  stage: Stage
  ms: number
  label: string
  caption: string
}

/**
 * Single knob for the demo script's pacing (~85s total, see the pitch runbook).
 * Change these five numbers — and only these — to retime a rehearsal.
 */
export const STAGE_MIN_MS: Record<RunStage, number> = {
  FORECASTING: 18_000,
  PAYING: 18_000,
  REASONING: 22_000,
  VALIDATING: 18_000,
  EXECUTING: 8_000,
}

const STAGE_SEQUENCE_META: Record<RunStage, { label: string; caption: string }> = {
  FORECASTING: { label: 'FORECASTING', caption: '예정 지출 분석 중' },
  PAYING: { label: 'PAYING', caption: '402 Payment Required' },
  REASONING: { label: 'REASONING', caption: 'Gemini 판단 중' },
  VALIDATING: { label: 'VALIDATING', caption: '정책 검증 중' },
  EXECUTING: { label: 'EXECUTING', caption: '온체인 실행 중' },
}

/**
 * `?speed=4` divides every stage's duration by 4 for a rehearsal run — no
 * rebuild needed. Omit the param (or `speed=1`) for the full ~85s pace.
 */
function rehearsalSpeed(): number {
  if (typeof window === 'undefined') return 1
  const raw = Number(new URLSearchParams(window.location.search).get('speed'))
  return Number.isFinite(raw) && raw > 0 ? raw : 1
}

const SPEED = rehearsalSpeed()

export const STAGE_SEQUENCE: StageSpec[] = RUN_STAGES.map((stage) => ({
  stage,
  ms: Math.max(200, Math.round(STAGE_MIN_MS[stage] / SPEED)),
  ...STAGE_SEQUENCE_META[stage],
}))

const TOTAL_MS = STAGE_SEQUENCE.reduce((sum, s) => sum + s.ms, 0)

const ORDER: Record<Stage, number> = STAGES.reduce(
  (acc, s, i) => ({ ...acc, [s]: i }),
  {} as Record<Stage, number>,
)

/** Has the run advanced at least as far as `target`? DONE satisfies everything. */
export function reached(current: Stage, target: Stage): boolean {
  return ORDER[current] >= ORDER[target]
}

export function isRunning(stage: Stage): boolean {
  return stage !== 'IDLE' && stage !== 'DONE'
}

/**
 * Before the first run the dashboard shows the snapshot in full (so the page is
 * never empty); once a run starts, cards gate themselves on their stage.
 */
export function revealed(stage: Stage, target: Stage, hasRun: boolean): boolean {
  if (!hasRun) return true
  return reached(stage, target)
}

export interface RunState {
  stage: Stage
  /** Index into STAGE_SEQUENCE, -1 when idle/done. */
  step: number
  /** True once a run has been started at least once in this session. */
  hasRun: boolean
  /** 0–100, drives the thin progress bar under the header. */
  progress: number
}

export const initialRunState: RunState = {
  stage: 'IDLE',
  step: -1,
  hasRun: false,
  progress: 0,
}

export type RunEvent = { type: 'START' } | { type: 'ADVANCE' } | { type: 'RESET' }

function progressAt(step: number): number {
  const elapsed = STAGE_SEQUENCE.slice(0, step).reduce((s, x) => s + x.ms, 0)
  return Math.round((elapsed / TOTAL_MS) * 100)
}

export function runReducer(state: RunState, event: RunEvent): RunState {
  switch (event.type) {
    case 'START':
      return { stage: STAGE_SEQUENCE[0].stage, step: 0, hasRun: true, progress: 0 }
    case 'ADVANCE': {
      const next = state.step + 1
      if (next >= STAGE_SEQUENCE.length) {
        return { ...state, stage: 'DONE', step: -1, progress: 100 }
      }
      return {
        ...state,
        stage: STAGE_SEQUENCE[next].stage,
        step: next,
        progress: progressAt(next),
      }
    }
    case 'RESET':
      return initialRunState
    default:
      return state
  }
}

export function currentSpec(state: RunState): StageSpec | null {
  return state.step >= 0 ? STAGE_SEQUENCE[state.step] : null
}
