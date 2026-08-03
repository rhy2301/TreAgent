import type { Snapshot } from '../types'
import { usdc } from '../lib/format'
import { walletByAddress } from '../lib/derive'
import { Badge, Card, CardTitle } from './ui'

const W = 620
const H = 250
const TOP_Y = 52
const BOTTOM_Y = 188
const NODE_W = 126
const NODE_H = 46

interface Node {
  id: string
  label: string
  x: number
  y: number
}

/**
 * Sources are whichever wallets the intents actually spend from — the layout is
 * derived, not a hardcoded "vault on top".
 */
function layout(snap: Snapshot): { nodes: Node[]; byId: Map<string, Node> } {
  const sourceAddresses = new Set(snap.intents.map((i) => i.from_address))
  const sources = snap.wallets.filter((w) => sourceAddresses.has(w.address))
  const rest = snap.wallets.filter((w) => !sourceAddresses.has(w.address))
  const top = sources.length > 0 ? sources : snap.wallets.slice(0, 1)
  const bottom = sources.length > 0 ? rest : snap.wallets.slice(1)

  const place = (list: typeof snap.wallets, y: number): Node[] =>
    list.map((w, i) => ({
      id: w.wallet_id,
      label: w.label,
      x: ((i + 1) * W) / (list.length + 1),
      y,
    }))

  const nodes = [...place(top, TOP_Y), ...place(bottom, BOTTOM_Y)]
  return { nodes, byId: new Map(nodes.map((n) => [n.id, n])) }
}

export function FundFlow({
  snap,
  executing,
  done,
}: {
  snap: Snapshot
  executing: boolean
  done: boolean
}) {
  const { nodes, byId } = layout(snap)

  const edges = snap.intents.flatMap((intent) => {
    const from = walletByAddress(snap, intent.from_address)
    const to = walletByAddress(snap, intent.to_address)
    if (!from || !to) return []
    const a = byId.get(from.wallet_id)
    const b = byId.get(to.wallet_id)
    if (!a || !b) return []
    const y1 = a.y + NODE_H / 2
    const y2 = b.y - NODE_H / 2
    const d = `M ${a.x} ${y1} C ${a.x} ${(y1 + y2) / 2}, ${b.x} ${(y1 + y2) / 2}, ${b.x} ${y2}`
    return [
      {
        key: intent.intent_id,
        d,
        amount: intent.amount_usdc,
        verdict: intent.guard_verdict,
        mx: (a.x + b.x) / 2,
        my: (y1 + y2) / 2,
      },
    ]
  })

  const activeIds = new Set<string>()
  for (const intent of snap.intents) {
    const from = walletByAddress(snap, intent.from_address)
    const to = walletByAddress(snap, intent.to_address)
    if (from) activeIds.add(from.wallet_id)
    if (to) activeIds.add(to.wallet_id)
  }

  return (
    <Card className="h-full">
      <CardTitle
        title="Fund Flow"
        scope={{ label: snap.networks.treasury.label, tone: 'mint' }}
        right={
          <Badge tone={done ? 'mint' : executing ? 'warning' : 'muted'}>
            {done ? 'Settled' : executing ? 'Executing…' : `${edges.length} routes`}
          </Badge>
        }
      />
      <div className="px-4 pb-4">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img">
          {/* Inactive edges are implied by absence — only intent routes are drawn. */}
          {edges.map((e) => (
            <g key={e.key}>
              <path
                d={e.d}
                fill="none"
                stroke={done ? '#43E6B0' : '#20324A'}
                strokeWidth={done ? 1.8 : 1.4}
                strokeDasharray={done ? undefined : '4 4'}
                className="transition-all duration-500"
              />
              {executing && (
                <circle
                  r="4"
                  fill="#43E6B0"
                  style={{
                    offsetPath: `path("${e.d}")`,
                    offsetRotate: '0deg',
                    animation: 'flow-dot 1.4s linear infinite',
                  }}
                />
              )}
              <rect
                x={e.mx - 42}
                y={e.my - 10}
                width="84"
                height="20"
                rx="6"
                fill="#101D30"
                stroke={done ? '#43E6B040' : '#20324A'}
              />
              <text
                x={e.mx}
                y={e.my + 4}
                textAnchor="middle"
                className="mono"
                fontSize="10.5"
                fill={done ? '#43E6B0' : '#94A3B8'}
              >
                {usdc(e.amount)}
              </text>
            </g>
          ))}

          {nodes.map((n) => {
            const active = activeIds.has(n.id)
            return (
              <g key={n.id}>
                <rect
                  x={n.x - NODE_W / 2}
                  y={n.y - NODE_H / 2}
                  width={NODE_W}
                  height={NODE_H}
                  rx="11"
                  fill={active ? '#14243A' : '#101D30'}
                  stroke={active ? (done ? '#43E6B0' : '#2F4A6B') : '#20324A'}
                  strokeWidth="1"
                  className="transition-all duration-300"
                />
                <text
                  x={n.x}
                  y={n.y - 3}
                  textAnchor="middle"
                  fontSize="11"
                  fill={active ? '#F4F7FB' : '#64748B'}
                >
                  {n.label}
                </text>
                <text
                  x={n.x}
                  y={n.y + 12}
                  textAnchor="middle"
                  className="mono"
                  fontSize="9.5"
                  fill="#64748B"
                >
                  {n.id}
                </text>
              </g>
            )
          })}
        </svg>

        {edges.length === 0 && (
          <p className="pb-2 text-center text-[11.5px] text-muted">
            집행 대상 intent 가 없습니다.
          </p>
        )}
      </div>
    </Card>
  )
}
