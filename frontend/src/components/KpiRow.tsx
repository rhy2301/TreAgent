import type { Snapshot } from '../types'
import { usdc } from '../lib/format'
import { daily } from '../lib/derive'
import { ProgressBar } from './ui'

function Tile({
  label,
  children,
  footer,
  danger,
}: {
  label: string
  children: React.ReactNode
  footer?: React.ReactNode
  danger?: boolean
}) {
  return (
    <div
      className={`rounded-[14px] border bg-card px-5 py-4 shadow-card transition-colors duration-150 hover:bg-elevated ${
        danger ? 'border-danger/45' : 'border-border'
      }`}
    >
      <div className="text-[11px] tracking-[0.06em] text-muted">{label}</div>
      <div className="mt-2.5">{children}</div>
      {footer && <div className="mt-2.5 text-[11px] text-txt-2">{footer}</div>}
    </div>
  )
}

function WarnIcon() {
  return (
    <svg viewBox="0 0 16 16" className="size-[15px] shrink-0" fill="none">
      <path
        d="M8 1.8 15 14H1L8 1.8Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path d="M8 6v3.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="11.6" r="0.8" fill="currentColor" />
    </svg>
  )
}

export function KpiRow({ snap }: { snap: Snapshot }) {
  const { spent, limit, pct } = daily(snap)
  const atRisk = snap.kpi.at_risk_count > 0

  return (
    <div className="grid grid-cols-4 gap-4">
      <Tile
        label="Total Treasury"
        footer={`지갑 ${snap.wallets.length}개 합산`}
      >
        <div className="mono text-[26px] leading-none font-semibold text-txt">
          {usdc(snap.kpi.total_treasury_usdc)}
          <span className="ml-1.5 text-[12px] font-normal text-muted">USDC</span>
        </div>
      </Tile>

      <Tile
        label="Scheduled Payments"
        footer={`${snap.kpi.scheduled_payments_count}건 · 72h 이내`}
      >
        <div className="mono text-[26px] leading-none font-semibold text-txt">
          {usdc(snap.kpi.scheduled_payments_usdc)}
          <span className="ml-1.5 text-[12px] font-normal text-muted">USDC</span>
        </div>
      </Tile>

      {/* Risk is signalled by colour *and* icon *and* wording. */}
      <Tile
        label="At-Risk Payments"
        danger={atRisk}
        footer={
          atRisk ? (
            <span className="flex items-center gap-1.5 text-danger">
              <WarnIcon />
              부족분 {usdc(snap.kpi.at_risk_usdc)} USDC
            </span>
          ) : (
            <span className="text-mint">위험 지출 없음</span>
          )
        }
      >
        <div
          className={`mono flex items-baseline gap-2 text-[26px] leading-none font-semibold ${
            atRisk ? 'text-danger' : 'text-txt'
          }`}
        >
          {atRisk && (
            <span className="text-danger">
              <WarnIcon />
            </span>
          )}
          {snap.kpi.at_risk_count}
          <span className="text-[12px] font-normal text-muted">건</span>
        </div>
      </Tile>

      <Tile
        label="Daily Execution Limit"
        footer={<ProgressBar pct={pct} tone={pct > 80 ? 'warning' : 'mint'} />}
      >
        <div className="mono text-[22px] leading-none font-semibold text-txt">
          {usdc(spent, 0)}
          <span className="mx-1.5 text-[15px] font-normal text-muted">/</span>
          <span className="text-[17px] font-normal text-txt-2">{usdc(limit, 0)}</span>
          <span className="ml-1.5 text-[12px] font-normal text-muted">USDC</span>
        </div>
      </Tile>
    </div>
  )
}
