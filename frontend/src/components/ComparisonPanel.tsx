import type { Comparison, Snapshot } from '../types'
import { usdc } from '../lib/format'
import { Badge, Card, CardTitle, Mono } from './ui'

function DiffRow({
  fallback,
  llm,
  highlight,
  star,
}: {
  fallback: React.ReactNode
  llm: React.ReactNode
  highlight?: boolean
  star?: boolean
}) {
  return (
    <div
      className={`grid grid-cols-2 gap-3 rounded-[9px] px-2.5 py-2 ${
        highlight ? 'bg-ai/8' : ''
      }`}
    >
      <div className="text-[11.5px] leading-relaxed text-muted">{fallback}</div>
      <div
        className={`flex gap-1.5 text-[11.5px] leading-relaxed ${
          highlight ? 'text-txt' : 'text-txt-2'
        }`}
      >
        <span className="min-w-0 flex-1">{llm}</span>
        {star && <span className="shrink-0 text-ai">★</span>}
      </div>
    </div>
  )
}

function Body({ c }: { c: Comparison }) {
  const delta = Number(c.total_amount_delta)
  const deltaTone =
    Number.isFinite(delta) && delta !== 0
      ? delta < 0
        ? 'text-mint'
        : 'text-warning'
      : 'text-txt-2'

  return (
    <div className="divide-y divide-border/60">
      <DiffRow fallback={c.fallback_summary_ko} llm={c.llm_summary_ko} />
      <DiffRow
        fallback={<Mono>집행 건수 기준</Mono>}
        llm={
          <Mono>
            건수 차이 {c.action_count_delta > 0 ? '+' : ''}
            {c.action_count_delta}
          </Mono>
        }
        highlight={c.action_count_delta !== 0}
        star={c.action_count_delta !== 0}
      />
      <DiffRow
        fallback={<Mono>총 집행 금액 기준</Mono>}
        llm={
          <Mono className={deltaTone}>
            {Number.isFinite(delta) && delta > 0 ? '+' : ''}
            {usdc(c.total_amount_delta)} USDC
          </Mono>
        }
        highlight={delta !== 0}
        star={delta !== 0}
      />
      {c.timing_differences.map((t) => (
        <DiffRow key={t} fallback="규칙 순서대로 즉시 집행" llm={t} highlight star />
      ))}
      {c.priority_differences.map((p) => (
        <DiffRow key={p} fallback="우선순위: 규칙 순" llm={p} highlight star />
      ))}
      {c.llm_only_insights.map((i) => (
        <DiffRow key={i} fallback="—" llm={`“${i}”`} highlight star />
      ))}
    </div>
  )
}

/**
 * `comparison` is null until compare.py lands (see demo.py:439). The panel says
 * so plainly rather than inventing differences.
 */
function Unavailable({ snap }: { snap: Snapshot }) {
  const isFallbackRun = snap.inference.degraded
  return (
    <div className="px-5 pb-5">
      <div className="rounded-[12px] border border-dashed border-border bg-elevated/30 px-4 py-5 text-center">
        <div className="text-[12px] text-txt-2">
          이 스냅샷에는 비교 데이터가 없습니다{' '}
          <Mono className="text-muted">(comparison: null)</Mono>
        </div>
        <p className="mx-auto mt-1.5 max-w-[440px] text-[11px] leading-relaxed text-muted">
          {isFallbackRun
            ? '이번 사이클은 게이팅으로 Gemini 호출을 건너뛰고 결정론적 폴백만 실행했습니다. 비교 대상이 되는 LLM 판단이 존재하지 않습니다.'
            : 'compare.py 가 폴백 계획과 Gemini 계획의 차이를 산출하면 이 패널이 채워집니다. 프론트는 스냅샷에 있는 값만 렌더합니다.'}
        </p>
      </div>
    </div>
  )
}

export function ComparisonPanel({ snap }: { snap: Snapshot }) {
  const c = snap.comparison

  return (
    <Card className="flex h-full flex-col">
      <CardTitle
        title="Gemini vs Rule-based Fallback"
        scope={{ label: 'Judgment Delta', tone: 'ai' }}
        right={
          c ? (
            <Badge tone="ai" mono>
              {c.timing_differences.length +
                c.priority_differences.length +
                c.llm_only_insights.length}{' '}
              diffs
            </Badge>
          ) : (
            <Badge tone="muted">unavailable</Badge>
          )
        }
      />

      {c ? (
        <div className="flex flex-1 flex-col px-5 pb-4">
          <div className="grid grid-cols-2 gap-3 border-b border-border pb-2">
            <div className="text-[11px] font-medium tracking-[0.06em] text-muted uppercase">
              Rule-based Fallback
            </div>
            <div className="text-[11px] font-medium tracking-[0.06em] text-ai uppercase">
              Gemini Judgment
            </div>
          </div>
          <Body c={c} />
          <p className="mt-auto border-t border-border pt-3 text-[10.5px] leading-relaxed text-muted italic">
            Gemini 없이도 폴백으로 안전하게 동작합니다. 위 차이가 Gemini가 기여한
            판단입니다.
          </p>
        </div>
      ) : (
        <Unavailable snap={snap} />
      )}
    </Card>
  )
}
