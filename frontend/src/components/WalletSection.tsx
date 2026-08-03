import type { Snapshot, Wallet } from '../types'
import { isPositive, shortHash, untilDue, usdc } from '../lib/format'
import { walletTone, WALLET_TONE_LABEL, type WalletTone } from '../lib/derive'
import { Badge, Card, CardTitle } from './ui'

const TONE_STYLE: Record<
  WalletTone,
  { badge: 'mint' | 'warning' | 'danger'; border: string; glyph: string }
> = {
  SAFE: { badge: 'mint', border: 'border-border', glyph: '✓' },
  WARNING: { badge: 'warning', border: 'border-warning/40', glyph: '⚠' },
  AT_RISK: { badge: 'danger', border: 'border-danger/50', glyph: '⚠' },
}

function Row({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between text-[11.5px]">
      <span className="text-muted">{label}</span>
      <span className={`mono ${tone ?? 'text-txt-2'}`}>{value}</span>
    </div>
  )
}

function WalletTile({
  wallet,
  now,
  showShortfall,
}: {
  wallet: Wallet
  now: string
  showShortfall: boolean
}) {
  const tone = walletTone(wallet)
  const style = TONE_STYLE[tone]
  const short = isPositive(wallet.shortfall)
  const due = untilDue(wallet.earliest_due_at, now)

  return (
    <div
      className={`rounded-[14px] border bg-elevated/60 px-4 py-3.5 transition-colors duration-150 hover:bg-elevated ${style.border}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[12.5px] font-medium text-txt">{wallet.label}</div>
          <div className="mono mt-0.5 text-[10px] text-muted" title={wallet.address}>
            {shortHash(wallet.address, 4, 4)}
          </div>
        </div>
        <Badge tone={style.badge} icon={<span aria-hidden>{style.glyph}</span>}>
          {WALLET_TONE_LABEL[tone]}
        </Badge>
      </div>

      <div className="mono mt-3 text-[21px] leading-none font-semibold text-txt">
        {usdc(wallet.usdc_balance)}
        <span className="ml-1.5 text-[11px] font-normal text-muted">USDC</span>
      </div>

      <div className="mt-3 space-y-1.5 border-t border-border/70 pt-2.5">
        <Row label="72h 예정 지출" value={usdc(wallet.required_outflow)} />
        <Row label="최소 버퍼" value={usdc(wallet.min_buffer_usdc)} />
        {short && showShortfall ? (
          <div className="flex items-baseline justify-between text-[11.5px]">
            <span className="flex items-center gap-1 text-danger">
              <span aria-hidden>⚠</span> 부족 금액
            </span>
            <span className="mono font-semibold text-danger">
              −{usdc(wallet.shortfall)}
            </span>
          </div>
        ) : (
          <Row
            label="부족 금액"
            value={short ? '분석 중' : '없음'}
            tone={short ? 'text-muted' : 'text-mint'}
          />
        )}
        {due && (
          <Row label="최근접 만기" value={due} tone={short ? 'text-warning' : 'text-txt-2'} />
        )}
      </div>
    </div>
  )
}

export function WalletSection({
  snap,
  showShortfall,
}: {
  snap: Snapshot
  showShortfall: boolean
}) {
  return (
    <Card className="h-full">
      <CardTitle
        title="Wallet Balance Overview"
        scope={{ label: snap.networks.treasury.label, tone: 'mint' }}
        sub="아래 4개 지갑은 모두 자금 집행 계층(온체인)에 있습니다."
      />
      <div className="grid grid-cols-2 gap-3 px-5 pt-1 pb-5">
        {snap.wallets.map((w) => (
          <WalletTile
            key={w.wallet_id}
            wallet={w}
            now={snap.generated_at}
            showShortfall={showShortfall}
          />
        ))}
      </div>
    </Card>
  )
}
