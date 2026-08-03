import type { Snapshot } from '../types'
import { clockTime } from '../lib/format'
import { StatusDot } from './ui'

/** Full product surface is listed; only Overview is wired for the demo. */
const NAV = [
  { id: 'overview', label: 'Overview', active: true },
  { id: 'wallets', label: 'Wallets', active: false },
  { id: 'schedule', label: 'Payment Schedule', active: false },
  { id: 'decisions', label: 'Agent Decisions', active: false },
  { id: 'transactions', label: 'Transactions', active: false },
  { id: 'policies', label: 'Policies', active: false },
  { id: 'settings', label: 'Agent Settings', active: false },
]

function NavIcon({ id }: { id: string }) {
  const paths: Record<string, string> = {
    overview: 'M3 3h6v7H3V3Zm0 9h6v3H3v-3Zm8-9h6v4h-6V3Zm0 6h6v6h-6V9Z',
    wallets: 'M2 5.5A1.5 1.5 0 0 1 3.5 4h11A1.5 1.5 0 0 1 16 5.5v8a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 2 13.5v-8Zm10 4a1 1 0 1 0 2 0 1 1 0 0 0-2 0Z',
    schedule: 'M4 3h10a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm0 4h11M6 2v3m6-3v3',
    decisions: 'M9 2 3 5v5c0 3.5 2.6 6.3 6 7 3.4-.7 6-3.5 6-7V5L9 2Zm0 5v4m0 2v.5',
    transactions: 'M3 6h10l-2.5-2.5M15 12H5l2.5 2.5',
    policies: 'M4 3h7l4 4v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm6 0v5h5',
    settings:
      'M9 6.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Zm6.2 2.5c0 .4 0 .8-.1 1.2l1.4 1-1.4 2.4-1.6-.6c-.6.5-1.3.9-2 1.1L11.2 17H8.4l-.3-1.9c-.7-.2-1.4-.6-2-1.1l-1.6.6-1.4-2.4 1.4-1a6.6 6.6 0 0 1 0-2.4l-1.4-1 1.4-2.4 1.6.6c.6-.5 1.3-.9 2-1.1L8.4 1h2.8l.3 1.9c.7.2 1.4.6 2 1.1l1.6-.6 1.4 2.4-1.4 1c.1.4.1.8.1 1.2Z',
  }
  return (
    <svg viewBox="0 0 18 18" className="size-[15px] shrink-0" fill="none">
      <path
        d={paths[id]}
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function Sidebar({ snap }: { snap: Snapshot | null }) {
  const treasury = snap?.networks.treasury
  const inference = snap?.networks.inference

  return (
    <aside className="flex w-[236px] shrink-0 flex-col border-r border-border bg-bg-2">
      <div className="flex items-center gap-2.5 px-5 pt-6 pb-7">
        <span className="grid size-8 place-items-center rounded-[10px] bg-gradient-to-br from-mint to-mint-dark text-[15px] font-bold text-bg shadow-[0_6px_18px_-8px_rgba(67,230,176,0.9)]">
          T
        </span>
        <div className="leading-tight">
          <div className="text-[14px] font-semibold tracking-tight text-txt">TreAgent</div>
          <div className="text-[10px] tracking-[0.12em] text-muted uppercase">
            Treasury Agent
          </div>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5 px-3">
        {NAV.map((item) =>
          item.active ? (
            <div
              key={item.id}
              className="relative flex items-center gap-2.5 rounded-[10px] bg-mint/10 px-3 py-2 text-[13px] font-medium text-mint"
            >
              <span className="absolute top-1/2 left-0 h-4 w-[2px] -translate-y-1/2 rounded-full bg-mint" />
              <NavIcon id={item.id} />
              {item.label}
            </div>
          ) : (
            <div
              key={item.id}
              aria-disabled
              className="flex cursor-not-allowed items-center gap-2.5 rounded-[10px] px-3 py-2 text-[13px] text-txt-2 opacity-40"
            >
              <NavIcon id={item.id} />
              {item.label}
            </div>
          ),
        )}
      </nav>

      <div className="mt-auto px-4 pb-5">
        {/* Two networks, two lines — never merged into one badge. */}
        <div className="rounded-[12px] border border-border bg-card px-3 py-3">
          <div className="mb-2 text-[9px] tracking-[0.16em] text-muted uppercase">
            Networks
          </div>

          <div className="border-l-2 border-warning/50 pl-2">
            <div className="flex min-w-0 items-center gap-2 text-[11px] text-txt-2">
              <StatusDot tone="warning" />
              <span className="truncate" title={treasury?.label}>
                {treasury?.label ?? 'Solana Devnet'}
              </span>
            </div>
            <div className="mt-0.5 flex items-baseline justify-between gap-2 pl-[15px]">
              <span className="text-[9px] tracking-wide text-muted">자금 집행</span>
              <span
                className="mono text-[10px] text-warning"
                title="이 데모는 실제 devnet 연결 없이 재현 가능한 시뮬레이션으로 실행됩니다."
              >
                SIMULATED
              </span>
            </div>
          </div>

          <div className="mt-2.5 border-l-2 border-warning/50 pl-2">
            <div className="flex min-w-0 items-center gap-2 text-[11px] text-txt-2">
              <StatusDot tone="warning" />
              <span className="truncate" title={inference?.label}>
                {inference?.label ?? 'pay.sh Sandbox'}
              </span>
            </div>
            <div className="mt-0.5 flex items-baseline justify-between gap-2 pl-[15px]">
              <span className="text-[9px] tracking-wide text-muted">x402 결제</span>
              <span
                className="mono text-[10px] text-warning"
                title="이 데모는 실제 pay.sh 호출 없이 재현 가능한 시뮬레이션으로 실행됩니다."
              >
                SIMULATED
              </span>
            </div>
          </div>
        </div>

        <div className="mt-3 flex items-center gap-2 px-1 text-[11px] text-mint">
          <StatusDot tone="mint" pulse />
          Agent {snap?.agent_status === 'PAUSED' ? 'Paused' : 'Running'}
        </div>
        <div className="mono mt-1 px-1 text-[10px] text-muted">
          Last sync {clockTime(snap?.generated_at)}
        </div>
      </div>
    </aside>
  )
}
