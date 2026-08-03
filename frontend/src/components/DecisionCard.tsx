import type { GuardVerdict, ProposedAction, Snapshot } from '../types'
import { dateTime, usdc } from '../lib/format'
import { guardFor, injectionDetected, walletById } from '../lib/derive'
import { Badge, Card, CardTitle, Mono, Skeleton } from './ui'
import { PolicyChecklist } from './PolicyChecklist'
import { SecurityPanel } from './SecurityPanel'

const RISK_TONE: Record<string, 'mint' | 'warning' | 'danger' | 'neutral'> = {
  LOW: 'mint',
  MEDIUM: 'warning',
  MODERATE: 'warning',
  HIGH: 'danger',
  CRITICAL: 'danger',
}

const SEVERITY_TONE: Record<string, 'mint' | 'warning' | 'danger' | 'info'> = {
  LOW: 'info',
  MEDIUM: 'warning',
  HIGH: 'danger',
  CRITICAL: 'danger',
}

const VERDICT: Record<
  GuardVerdict,
  { tone: 'mint' | 'warning' | 'danger' | 'info'; label: string; glyph: string }
> = {
  APPROVED: { tone: 'mint', label: 'Executed Automatically', glyph: '✓' },
  CLAMPED: { tone: 'warning', label: 'Executed with Policy Clamp', glyph: '⚠' },
  REJECTED: { tone: 'danger', label: 'Blocked by Policy', glyph: '✕' },
  HELD: { tone: 'info', label: 'Held for Human Review', glyph: '⏸' },
}

function verdictOf(v: GuardVerdict) {
  return VERDICT[v] ?? { tone: 'neutral' as const, label: v, glyph: '·' }
}

/** Confidence as a thin arc-free gauge — no chart library, just a track. */
function ConfidenceGauge({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] tracking-[0.08em] text-muted uppercase">
        Confidence
      </span>
      <div className="h-[5px] w-[92px] overflow-hidden rounded-full bg-elevated">
        <div
          className="h-full rounded-full bg-gradient-to-r from-ai to-mint transition-[width] duration-700 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <Mono className="text-[12px] font-semibold text-txt">{value.toFixed(2)}</Mono>
    </div>
  )
}

function Field({
  label,
  children,
  wide,
}: {
  label: string
  children: React.ReactNode
  wide?: boolean
}) {
  return (
    <div className={wide ? 'col-span-2' : ''}>
      <div className="text-[10px] tracking-[0.08em] text-muted uppercase">{label}</div>
      <div className="mt-1 text-[12.5px] text-txt-2">{children}</div>
    </div>
  )
}

function ActionBlock({
  snap,
  action,
  checkCount,
  fromLLM,
}: {
  snap: Snapshot
  action: ProposedAction
  checkCount: number
  /** False when gating skipped the LLM — the rationale is then rule-based. */
  fromLLM: boolean
}) {
  const guard = guardFor(snap, action.action_id)
  const from = walletById(snap, action.from_wallet_id)
  const to = walletById(snap, action.to_wallet_id)
  const clamped = guard ? guard.requested_amount !== guard.final_amount : false
  const v = guard ? verdictOf(guard.verdict) : null

  return (
    <div className="rounded-[14px] border border-border bg-elevated/35 px-4 py-3.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="grid size-[20px] place-items-center rounded-md bg-ai/15 text-[10px] font-semibold text-ai">
            {action.priority}
          </span>
          <Mono className="text-[12px] text-txt">{action.action_id}</Mono>
          <Badge tone="ai" mono>
            {action.type}
          </Badge>
        </div>
        {v && guard && (
          <Badge tone={v.tone} icon={<span aria-hidden>{v.glyph}</span>}>
            {v.label}
          </Badge>
        )}
      </div>

      <div className="mt-3 grid grid-cols-4 gap-4">
        <Field label="Recommended Action">
          <Mono className="text-txt">{action.type}</Mono>
        </Field>

        <Field label="Amount">
          {guard ? (
            clamped ? (
              <span className="flex items-center gap-1.5">
                <Mono className="text-muted line-through">
                  {usdc(guard.requested_amount)}
                </Mono>
                <span className="text-warning">→</span>
                <Mono className="font-semibold text-warning">
                  {usdc(guard.final_amount)}
                </Mono>
              </span>
            ) : (
              <Mono className="font-semibold text-txt">{usdc(guard.final_amount)}</Mono>
            )
          ) : (
            <span className="text-muted">—</span>
          )}
        </Field>

        <Field label="Route">
          <span className="flex items-center gap-1.5">
            <Mono className="text-txt-2">{from?.label ?? action.from_wallet_id}</Mono>
            <span className="text-mint">→</span>
            <Mono className="text-txt-2">{to?.label ?? action.to_wallet_id}</Mono>
          </span>
        </Field>

        <Field label="Execution Time">
          {action.timing === 'SCHEDULED' ? (
            <span className="flex items-center gap-1.5">
              <Badge tone="info">SCHEDULED</Badge>
              <Mono className="text-[11px] text-txt-2">
                {dateTime(action.scheduled_for)}
              </Mono>
            </span>
          ) : (
            <Badge tone="mint">NOW</Badge>
          )}
        </Field>
      </div>

      {/* Explainable AI: the model's own reasoning, quoted, not paraphrased. */}
      <div className="mt-3.5">
        <div
          className={`text-[10px] tracking-[0.08em] uppercase ${
            fromLLM ? 'text-ai' : 'text-muted'
          }`}
        >
          {fromLLM ? 'AI Reasoning' : 'Fallback Rationale (rule-based)'}
        </div>
        <blockquote
          className={`mt-1.5 rounded-r-[10px] border-l-2 py-2.5 pr-3 pl-3.5 text-[13.5px] leading-[1.65] text-txt ${
            fromLLM ? 'border-ai/70 bg-ai/6' : 'border-border bg-elevated/40'
          }`}
        >
          “{action.rationale_ko}”
        </blockquote>
      </div>

      {guard && (
        <div className="mt-3.5">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[10px] tracking-[0.08em] text-muted uppercase">
              Policy Validation
            </span>
            <Mono className="text-[10px] text-muted">
              {guard.checks.length} checks · PolicyGuard
            </Mono>
          </div>
          <PolicyChecklist guard={guard} visibleCount={checkCount} />
          {guard.verdict === 'CLAMPED' && (
            <p className="mt-2 text-[11px] text-warning">
              LLM 요청 {usdc(guard.requested_amount)} → 정책 적용{' '}
              {usdc(guard.final_amount)}
            </p>
          )}
          {guard.notes.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {guard.notes.map((n) => (
                <li key={n} className="mono text-[10.5px] text-muted">
                  {n}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

export function DecisionCard({
  snap,
  ready,
  checkCount,
}: {
  snap: Snapshot
  /** REASONING finished — swap skeletons for the plan. */
  ready: boolean
  /** How many guard checks to reveal (VALIDATING staggers them). */
  checkCount: number
}) {
  const plan = snap.plan
  const riskTone = RISK_TONE[plan.overall_risk?.toUpperCase()] ?? 'neutral'
  const actions = [...plan.actions].sort((a, b) => a.priority - b.priority)
  const showSecurity = injectionDetected(snap)
  // A degraded cycle never reached the model; say so instead of implying Gemini.
  const fromLLM = !snap.inference.degraded

  return (
    <Card accent="ai">
      <div className="rounded-t-[15px] bg-gradient-to-r from-ai/12 via-ai/5 to-transparent">
        <CardTitle
          title="AI Decision"
          scope={{
            label: fromLLM ? 'Explainable AI' : 'Deterministic Fallback',
            tone: fromLLM ? 'ai' : 'warning',
          }}
          sub={
            <span className="flex items-center gap-2">
              <Mono>{fromLLM ? (snap.inference.model ?? '—') : 'forecaster.build_fallback_plan()'}</Mono>
              <span className="text-border">·</span>
              <Mono>{snap.decision_id}</Mono>
            </span>
          }
          right={
            <div className="flex items-center gap-4">
              <ConfidenceGauge value={plan.confidence} />
              <Badge tone={riskTone} icon={<span aria-hidden>⚑</span>}>
                RISK {plan.overall_risk}
              </Badge>
            </div>
          }
        />
      </div>

      {showSecurity && <SecurityPanel snap={snap} />}

      {!ready ? (
        <div className="space-y-2.5 px-5 pb-5">
          <Skeleton className="h-4 w-[42%]" />
          <Skeleton className="h-[76px] w-full" />
          <Skeleton className="h-4 w-[64%]" />
          <Skeleton className="h-[120px] w-full" />
          <div className="mono pt-1 text-[11px] text-ai">Gemini 판단 중…</div>
        </div>
      ) : (
        <div className="px-5 pb-5">
          {/* Detected risk */}
          <div className="mb-3.5">
            <div className="text-[10px] tracking-[0.08em] text-muted uppercase">
              Detected Risk
            </div>
            <div className="mt-1.5 grid grid-cols-2 gap-2">
              {plan.at_risk.map((r) => (
                <div
                  key={r.obligation_id}
                  className="flex items-start gap-2.5 rounded-[10px] border border-border bg-elevated/40 px-3 py-2"
                >
                  <Badge tone={SEVERITY_TONE[r.severity?.toUpperCase()] ?? 'neutral'}>
                    {r.severity}
                  </Badge>
                  <div className="min-w-0">
                    <Mono className="text-[11px] text-txt">{r.obligation_id}</Mono>
                    <p className="mt-0.5 text-[11.5px] leading-relaxed text-txt-2">
                      {r.reason_ko}
                    </p>
                  </div>
                </div>
              ))}
              {plan.at_risk.length === 0 && (
                <div className="text-[12px] text-mint">위험으로 분류된 지출 없음</div>
              )}
            </div>
          </div>

          <p className="mb-3.5 rounded-[10px] border border-border bg-elevated/30 px-3.5 py-2.5 text-[12px] leading-relaxed text-txt-2">
            <span className="text-muted">판단 요약 · </span>
            {plan.reasoning_summary_ko}
          </p>

          <div className="space-y-3">
            {actions.map((a) => (
              <ActionBlock
                key={a.action_id}
                snap={snap}
                action={a}
                checkCount={checkCount}
                fromLLM={fromLLM}
              />
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}
