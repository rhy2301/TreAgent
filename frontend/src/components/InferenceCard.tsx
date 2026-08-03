import { useEffect, useRef, useState } from 'react'
import type { Inference, Snapshot } from '../types'
import { num, usdc, usdcFine } from '../lib/format'
import { hasPaymentFlow, inferenceLinkable, providerMode } from '../lib/derive'
import { Badge, Card, CardTitle, CopyButton, Info, Mono, Skeleton } from './ui'

const PROJECTED_TOOLTIP =
  '실제 온체인 잔고가 아닌 예상값입니다. 추론 비용을 운영비로 반영했을 때의 잔고입니다.'

/** Latency counts up while REASONING runs, then settles on the real value. */
function useLatencyCounter(target: number | null, running: boolean, done: boolean) {
  const [shown, setShown] = useState(0)
  const raf = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (!target) {
      setShown(0)
      return
    }
    if (done) {
      setShown(target)
      return
    }
    if (!running) return

    const started = performance.now()
    const tick = (t: number) => {
      const elapsed = t - started
      // Track the scripted REASONING window (2500ms) but display real ms.
      const ratio = Math.min(1, elapsed / 2500)
      setShown(Math.round(target * ratio))
      if (ratio < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current)
    }
  }, [target, running, done])

  return shown
}

function StepArrow() {
  return (
    <div className="my-1 ml-[9px] h-3 w-px bg-gradient-to-b from-ai/50 to-transparent" />
  )
}

function FlowStep({
  active,
  tone,
  left,
  right,
  glyph,
}: {
  active: boolean
  tone: 'warning' | 'mint' | 'ai'
  left: React.ReactNode
  right?: React.ReactNode
  glyph?: string
}) {
  const color =
    tone === 'warning' ? 'text-warning' : tone === 'mint' ? 'text-mint' : 'text-ai'
  const ring =
    tone === 'warning'
      ? 'border-warning/45 bg-warning/8'
      : tone === 'mint'
        ? 'border-mint/40 bg-mint/8'
        : 'border-ai/40 bg-ai/8'
  return (
    <div
      className={`flex items-center justify-between gap-3 rounded-[10px] border px-3 py-2 transition-all duration-200 ${
        active ? ring : 'border-border bg-elevated/40'
      }`}
    >
      <span
        className={`text-[12px] font-medium transition-colors duration-200 ${
          active ? color : 'text-muted'
        }`}
      >
        {left}
      </span>
      <span className={`flex items-center gap-2 text-[12px] ${active ? color : 'text-muted'}`}>
        {right}
        {glyph && (
          <span
            className={`grid size-[18px] place-items-center rounded-full border text-[10px] ${
              active ? `${ring} ${color}` : 'border-border text-muted'
            }`}
          >
            {glyph}
          </span>
        )}
      </span>
    </div>
  )
}

function BalanceLine({
  label,
  value,
  tone = 'text-txt-2',
  info,
}: {
  label: React.ReactNode
  value: string
  tone?: string
  info?: string
}) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-[11.5px]">
      <span className="flex items-center text-muted">
        {label}
        {info && <Info text={info} />}
      </span>
      <Mono className={tone}>{value}</Mono>
    </div>
  )
}

function DegradedBanner({ inf }: { inf: Inference }) {
  return (
    <div className="mx-5 mb-3 rounded-[10px] border border-warning/45 bg-warning/10 px-3 py-2.5">
      <div className="flex items-center gap-2 text-[12px] font-semibold text-warning">
        <span aria-hidden>⚠</span> AI Studio 폴백 — x402 결제 미발생
      </div>
      {inf.degraded_reason && (
        <p className="mt-1 text-[11px] leading-relaxed text-txt-2">{inf.degraded_reason}</p>
      )}
    </div>
  )
}

function MockBanner() {
  return (
    <div className="mx-5 mb-3 rounded-[10px] border border-border bg-elevated px-3 py-2.5 text-[12px] text-muted">
      <span aria-hidden className="mr-1.5">
        ○
      </span>
      Mock provider — 실제 추론 없음
    </div>
  )
}

export function InferenceCard({
  snap,
  lit,
  reasoning,
  reasoningDone,
}: {
  snap: Snapshot
  /** PAYING reached — the x402 flow lights up. */
  lit: boolean
  reasoning: boolean
  reasoningDone: boolean
}) {
  const inf = snap.inference
  const mode = providerMode(inf)
  const flowRan = hasPaymentFlow(inf)
  const linkable = inferenceLinkable(inf)
  const latency = useLatencyCounter(inf.latency_ms, reasoning, reasoningDone)

  // A degraded run made no x402 payment, so its sandbox delta line is omitted.
  const showSandboxLine = mode === 'LIVE' && inf.sandbox_balance_before !== null

  return (
    <Card accent="ai" className="flex h-full flex-col">
      <CardTitle
        title="Inference Payment (x402)"
        scope={{ label: 'Payment Protocol Layer', tone: 'ai' }}
        stack
      />

      {mode === 'DEGRADED' && <DegradedBanner inf={inf} />}
      {mode === 'MOCK' && <MockBanner />}

      <div className={`flex flex-1 flex-col px-5 pb-4 ${mode === 'MOCK' ? 'opacity-45' : ''}`}>
        {/* Three distinct badges: provider · mode · network. Never merged. */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="ai" mono>
            {inf.provider}
          </Badge>
          <Badge tone="neutral" mono>
            {inf.payment_mode ?? 'no payment'}
          </Badge>
          <Badge tone="muted" mono>
            {inf.payment_network ?? 'not used'}
          </Badge>
        </div>
        <div className="mono mt-2 text-[13px] font-medium text-txt">
          {inf.model ?? '—'}
        </div>

        {/* 402 → payment → 200 */}
        <div
          className={`mt-3.5 transition-opacity duration-200 ${
            flowRan ? '' : 'pointer-events-none opacity-35 grayscale'
          }`}
        >
          <FlowStep
            active={lit && flowRan}
            tone="warning"
            left={`HTTP ${inf.challenge_status ?? 402} Payment Required`}
            glyph="!"
          />
          <StepArrow />
          <FlowStep
            active={lit && flowRan}
            tone="mint"
            left="Sandbox Paid"
            right={
              <Mono className="font-semibold">
                {inf.cost_usdc === null ? '—' : `${usdcFine(inf.cost_usdc)} USDC`}
              </Mono>
            }
          />
          <StepArrow />
          <FlowStep
            active={lit && flowRan}
            tone="mint"
            left={`HTTP ${inf.response_status ?? 200} OK`}
            glyph="✓"
          />
        </div>

        {/* Signature. Linked out only when an explorer URL exists. */}
        <div className="mt-3.5 rounded-[10px] border border-border bg-elevated/50 px-3 py-2.5">
          <div className="text-[10px] tracking-[0.08em] text-muted uppercase">
            {linkable ? 'Transaction' : 'Sandbox payment proof'}
          </div>
          {inf.payment_signature ? (
            <div className="mt-1.5 flex items-center justify-between gap-2">
              {linkable ? (
                <a
                  href={inf.explorer_url!}
                  target="_blank"
                  rel="noreferrer"
                  className="mono truncate text-[11.5px] text-info underline decoration-info/40 underline-offset-2 transition-colors duration-150 hover:text-mint"
                >
                  {inf.payment_signature.slice(0, 6)}…{inf.payment_signature.slice(-4)} ↗
                </a>
              ) : (
                <span
                  className="mono truncate text-[11.5px] text-txt-2"
                  title={inf.payment_signature}
                >
                  {inf.payment_signature.slice(0, 6)}…{inf.payment_signature.slice(-4)}
                </span>
              )}
              <CopyButton value={inf.payment_signature} />
            </div>
          ) : (
            <div className="mono mt-1.5 text-[11.5px] text-muted">서명 없음</div>
          )}

          <div className="mono mt-2 flex items-center gap-2 text-[10.5px] text-muted">
            {reasoning && !reasoningDone ? (
              <Skeleton className="h-3 w-40" />
            ) : (
              <>
                <span>
                  Latency{' '}
                  <span className="text-txt-2">
                    {inf.latency_ms === null ? '—' : `${num(latency)} ms`}
                  </span>
                </span>
                <span className="text-border">·</span>
                <span>
                  {num(inf.input_tokens)} → {num(inf.output_tokens)} tok
                </span>
              </>
            )}
          </div>
        </div>

        {/* Balances stay on separate lines: two networks, never summed. */}
        <div className="mt-3.5 space-y-1.5 border-t border-border pt-3">
          <BalanceLine
            label="Devnet Operations Wallet"
            value={
              inf.devnet_ops_balance === null
                ? '—'
                : `${usdcFine(inf.devnet_ops_balance)} USDC`
            }
            tone="text-txt"
          />
          {showSandboxLine && (
            <BalanceLine
              label="Sandbox inference payment"
              value={inf.cost_usdc === null ? '—' : `−${usdcFine(inf.cost_usdc)} USDC`}
              tone="text-ai"
            />
          )}
          <BalanceLine
            label="Projected operating balance"
            value={
              inf.projected_ops_balance === null
                ? '—'
                : `${usdcFine(inf.projected_ops_balance)} USDC`
            }
            tone="text-txt-2"
            info={PROJECTED_TOOLTIP}
          />
          <BalanceLine
            label="Inference runway"
            value={
              inf.estimated_runway_days === null
                ? '—'
                : `~${usdc(inf.estimated_runway_days, 1)} days`
            }
          />
        </div>

        {/* The recursion: this cycle's inference cost funds next cycle's forecast. */}
        {inf.booked_as_obligation_id && (
          <div className="mt-3 flex items-center justify-between gap-2 rounded-[10px] border border-ai/30 bg-ai/8 px-3 py-2">
            <span className="text-[11px] text-txt-2">
              Booked as{' '}
              <Mono className="text-ai">{inf.booked_as_obligation_id}</Mono>
            </span>
            <Badge tone="ai" mono>
              API_USAGE
            </Badge>
          </div>
        )}

        <p className="mt-3 border-t border-border pt-3 text-[10.5px] leading-[1.6] text-muted">
          TreAgent의 자금 집행은 <span className="text-mint">Solana Devnet에서 실제 실행</span>
          되며, Gemini x402 결제 흐름은{' '}
          <span className="text-ai">pay.sh 공식 샌드박스에서 검증</span>되었습니다. 추론
          비용은 <Mono className="text-txt-2">API_USAGE</Mono> 운영비로 기표되어 다음 판단
          사이클의 예측에 반영됩니다.
        </p>
      </div>
    </Card>
  )
}
