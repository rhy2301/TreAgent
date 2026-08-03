import type { Snapshot } from '../types'
import {
  extractInjectedMemo,
  plannedWalletIds,
  whitelistedWalletIds,
} from '../lib/derive'
import { Badge, Mono } from './ui'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 text-[11.5px] leading-relaxed">
      <span className="w-[76px] shrink-0 text-muted">{label}</span>
      <span className="min-w-0 flex-1 text-txt-2">{children}</span>
    </div>
  )
}

function Layer({
  tag,
  title,
  lines,
  tone,
}: {
  tag: string
  title: string
  lines: string[]
  tone: 'ai' | 'mint'
}) {
  const style =
    tone === 'ai' ? 'border-ai/40 bg-ai/8' : 'border-mint/35 bg-mint/8'
  const text = tone === 'ai' ? 'text-ai' : 'text-mint'
  return (
    <div className={`flex-1 rounded-[12px] border px-3.5 py-3 ${style}`}>
      <div className={`text-[10px] tracking-[0.1em] uppercase ${text}`}>{tag}</div>
      <div className="mt-1 text-[12.5px] font-semibold text-txt">{title}</div>
      <ul className="mt-2 space-y-1">
        {lines.map((l) => (
          <li key={l} className="flex gap-1.5 text-[11px] leading-relaxed text-txt-2">
            <span className={`shrink-0 ${text}`}>·</span>
            {l}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Two-layer defence readout for the prompt_injection scenario.
 * Everything here is derived: the quoted memo is pulled out of
 * `escalation_reason_ko`, and the "no attacker wallet" claim is checked against
 * the plan's own routing rather than asserted.
 */
export function SecurityPanel({ snap }: { snap: Snapshot }) {
  const reason = snap.plan.escalation_reason_ko
  const memo = extractInjectedMemo(reason)
  const whitelist = whitelistedWalletIds(snap)
  const planned = plannedWalletIds(snap)
  const offPlan = planned.filter((id) => !whitelist.includes(id))
  const guardWhitelistChecks = snap.guard.flatMap((g) =>
    g.checks.filter((c) => String(c.id).toUpperCase().includes('WHITELIST')),
  )
  const whitelistAllPass = guardWhitelistChecks.every(
    (c) => String(c.result).toUpperCase() === 'PASS',
  )

  return (
    <div className="mx-5 mb-4 rounded-[14px] border border-danger/45 bg-danger/8">
      <div className="flex items-center gap-2.5 border-b border-danger/25 px-4 py-3">
        <span className="grid size-[22px] place-items-center rounded-md bg-danger/15 text-[12px] text-danger">
          ⚠
        </span>
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-danger">
            Prompt injection detected
          </div>
          <p className="mt-0.5 text-[11px] leading-relaxed text-txt-2">
            외부 메모에 포함된 송금 지시를 무시했습니다. Gemini가 공격을 탐지했으며
            PolicyGuard가 지갑 식별자를 독립적으로 재검증했습니다.
          </p>
        </div>
        <div className="ml-auto flex shrink-0 gap-1.5">
          <Badge tone="danger" mono>
            PROMPT_INJECTION
          </Badge>
          {snap.plan.escalation_required && (
            <Badge tone="info">Escalation Required</Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-5 px-4 py-3.5">
        <div className="space-y-1.5">
          <Field label="탐지 유형">
            <Mono className="text-danger">PROMPT_INJECTION</Mono>
          </Field>
          <Field label="공격 출처">
            <Mono className="text-txt-2">obligations[].memo</Mono>
          </Field>
          <Field label="공격 목표">
            미등록 wallet_id <Mono className="text-danger">"attacker_wallet"</Mono>로 송금 유도
          </Field>
          <Field label="LLM 대응">
            악성 지시 무시 — 정책 기반 부족분 계산만 수행
          </Field>
          <Field label="생성 액션">
            {offPlan.length === 0 ? (
              <span className="text-mint">
                ✓ 생성된 {snap.plan.actions.length}건 모두 등록 지갑({planned.join(', ')})
                으로만 라우팅됨 — attacker_wallet 미포함
              </span>
            ) : (
              <span className="text-danger">
                ✕ 미등록 대상 포함: {offPlan.join(', ')}
              </span>
            )}
          </Field>
        </div>

        <div>
          <div className="text-[10px] tracking-[0.08em] text-muted uppercase">
            Untrusted input
          </div>
          {memo ? (
            <div className="mt-1.5 rounded-[10px] border border-danger/35 bg-bg/70 p-2.5">
              <div className="mb-1.5 flex justify-end">
                <Badge tone="danger">Untrusted input — ignored</Badge>
              </div>
              <pre className="mono max-h-[74px] overflow-hidden text-[10.5px] leading-relaxed break-words whitespace-pre-wrap text-danger/85">
                {memo.length > 160 ? `${memo.slice(0, 160)}…` : memo}
              </pre>
            </div>
          ) : (
            <div className="mt-1.5 text-[11px] text-muted">
              메모 원문은 스냅샷에 포함되지 않았습니다.
            </div>
          )}
        </div>
      </div>

      {reason && (
        <div className="px-4 pb-3.5">
          <div className="text-[10px] tracking-[0.08em] text-muted uppercase">
            Escalation reason
          </div>
          <p className="mt-1 rounded-[10px] border border-border bg-bg/50 px-3 py-2 text-[11.5px] leading-[1.7] text-txt-2">
            {reason}
          </p>
        </div>
      )}

      {/* 1차 방어 → 2차 방어 */}
      <div className="flex flex-col px-4 pb-4">
        <Layer
          tag="1차 방어 — Gemini"
          title="악성 memo 무시"
          lines={['외부 memo 를 데이터로만 취급', '정상 funding option 만 선택']}
          tone="ai"
        />
        <div className="py-1 text-center text-[15px] leading-none text-muted">↓</div>
        <Layer
          tag="2차 방어 — PolicyGuard"
          title="wallet_id 화이트리스트 재검증"
          lines={[
            `등록 지갑 ${whitelist.length}개와 대조 — 미등록 주소 차단`,
            guardWhitelistChecks.length > 0
              ? `WHITELIST 검사 ${guardWhitelistChecks.length}건 ${
                  whitelistAllPass ? '전부 PASS' : '실패 포함'
                }`
              : 'WHITELIST 검사 결과 없음',
          ]}
          tone="mint"
        />
      </div>
    </div>
  )
}
