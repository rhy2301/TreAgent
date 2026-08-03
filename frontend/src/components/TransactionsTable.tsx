import type { Snapshot, TxRow } from '../types'
import { clockTime, shortHash, usdc } from '../lib/format'
import { isInferenceTx, isLinkable } from '../lib/derive'
import { Badge, Card, CardTitle, CopyButton, Mono, StatusDot } from './ui'

const STATUS: Record<string, { tone: 'mint' | 'warning' | 'danger'; pulse: boolean }> = {
  CONFIRMED: { tone: 'mint', pulse: false },
  PENDING: { tone: 'warning', pulse: true },
  FAILED: { tone: 'danger', pulse: false },
}

function NetworkBadge({ row }: { row: TxRow }) {
  // Tone follows the layer the row belongs to; the *link* decision is made
  // separately, from explorer_url alone.
  return (
    <Badge tone={isInferenceTx(row) ? 'ai' : 'mint'} mono>
      {row.network}
    </Badge>
  )
}

function SignatureCell({ row }: { row: TxRow }) {
  if (!row.signature) {
    return <span className="text-[11px] text-muted">—</span>
  }
  // Sole rule: an explorer URL makes it linkable. Never inspect `network`.
  if (isLinkable(row)) {
    return (
      <a
        href={row.explorer_url!}
        target="_blank"
        rel="noreferrer"
        className="mono text-[11.5px] text-info underline decoration-info/40 underline-offset-2 transition-colors duration-150 hover:text-mint"
        title={row.signature}
      >
        {shortHash(row.signature)} ↗
      </a>
    )
  }
  return (
    <span className="flex items-center gap-2">
      <Badge tone="ai">Sandbox Proof</Badge>
      <Mono className="text-[11px] text-txt-2" title={row.signature}>
        {shortHash(row.signature)}
      </Mono>
      <CopyButton value={row.signature} />
    </span>
  )
}

export function TransactionsTable({ snap }: { snap: Snapshot }) {
  return (
    <Card>
      <CardTitle
        title="Recent Transactions"
        sub="자금 집행과 추론 결제가 한 표에 있습니다. Network 컬럼으로 계층을 구분하세요."
        right={
          <Mono className="text-[11px] text-muted">{snap.transactions.length} rows</Mono>
        }
      />
      <div className="overflow-x-auto px-5 pb-5">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-border text-[10px] tracking-[0.08em] text-muted uppercase">
              <th className="py-2 pr-3 font-medium">Time</th>
              <th className="py-2 pr-3 font-medium">Type</th>
              <th className="py-2 pr-3 font-medium">Network</th>
              <th className="py-2 pr-3 font-medium">From</th>
              <th className="py-2 pr-3 font-medium">To</th>
              <th className="py-2 pr-3 text-right font-medium">Amount</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 font-medium">Transaction</th>
            </tr>
          </thead>
          <tbody>
            {snap.transactions.map((row, i) => {
              const inference = isInferenceTx(row)
              const status = STATUS[row.status.toUpperCase()] ?? {
                tone: 'warning' as const,
                pulse: false,
              }
              return (
                <tr
                  key={`${row.signature ?? row.type}-${i}`}
                  className={`border-b border-border/50 transition-colors duration-150 hover:bg-elevated/60 ${
                    inference ? 'bg-ai/5' : ''
                  }`}
                >
                  <td className="mono py-2.5 pr-3 text-[11.5px] text-txt-2">
                    {clockTime(row.time)}
                  </td>
                  <td className="py-2.5 pr-3">
                    <Mono
                      className={`text-[11.5px] ${inference ? 'text-ai' : 'text-txt-2'}`}
                    >
                      {row.type}
                    </Mono>
                  </td>
                  <td className="py-2.5 pr-3">
                    <NetworkBadge row={row} />
                  </td>
                  <td className="py-2.5 pr-3 text-[11.5px] text-txt-2">
                    {row.from_label}
                  </td>
                  <td className="py-2.5 pr-3 text-[11.5px] text-txt-2">{row.to_label}</td>
                  <td className="mono py-2.5 pr-3 text-right text-[12px] font-medium text-txt">
                    {usdc(row.amount_usdc, inference ? 4 : 2)}
                  </td>
                  <td className="py-2.5 pr-3">
                    <span className="flex items-center gap-1.5 text-[11px] text-txt-2">
                      <StatusDot tone={status.tone} pulse={status.pulse} />
                      {row.status}
                    </span>
                  </td>
                  <td className="py-2.5">
                    <SignatureCell row={row} />
                  </td>
                </tr>
              )
            })}
            {snap.transactions.length === 0 && (
              <tr>
                <td colSpan={8} className="py-6 text-center text-[12px] text-muted">
                  거래 없음
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
