import type { Snapshot, TxRow, Wallet } from '../types'
import type { Stage } from './stages'
import { reached } from './stages'

/**
 * There is no live backend for a Run Agent click — the FastAPI execution
 * endpoints and services/executor.py are stubs, and demo.py only produces a
 * static snapshot file (see README "현재 구현 상태"). Everything in this file
 * is a client-side simulation so the demo shows state actually changing
 * instead of replaying the same fixture. No real Solana devnet or pay.sh
 * sandbox call happens here.
 */

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

function randomBase58(len: number): string {
  let out = ''
  for (let i = 0; i < len; i++) out += B58[Math.floor(Math.random() * B58.length)]
  return out
}

function randomHex(len: number): string {
  let out = ''
  for (let i = 0; i < len; i++) out += Math.floor(Math.random() * 16).toString(16)
  return out
}

function n(v: string | number | null | undefined): number {
  const x = Number(v)
  return Number.isFinite(x) ? x : 0
}

function recomputeWallet(w: Wallet): Wallet {
  const shortfall = Math.max(0, n(w.required_outflow) + n(w.min_buffer_usdc) - n(w.usdc_balance))
  return {
    ...w,
    shortfall: shortfall.toFixed(6),
    status: shortfall > 0 ? 'SHORTFALL' : 'HEALTHY',
  }
}

/**
 * Runs one judgment → x402 payment → execution cycle on top of the currently
 * committed snapshot and returns the resulting snapshot. Pure function, no
 * side effects — the caller decides when to reveal which part of the result.
 */
export function runExecutionCycle(committed: Snapshot): Snapshot {
  const nowIso = new Date().toISOString()
  const wallets = new Map<string, Wallet>(committed.wallets.map((w) => [w.wallet_id, { ...w }]))
  const newTx: TxRow[] = []
  let inference = committed.inference

  // 1. x402 inference payment — skipped when this cycle was gated/degraded,
  // same as the real INFERENCE_BUDGET_LOW fallback path never calling out.
  if (!committed.inference.degraded) {
    const cost = +(0.04 + Math.random() * 0.02).toFixed(6)
    const ops = wallets.get('operations')
    if (ops) {
      ops.usdc_balance = Math.max(0, n(ops.usdc_balance) - cost).toFixed(6)
    }
    const sandboxBefore = n(
      committed.inference.sandbox_balance_after ?? committed.inference.sandbox_balance_before,
    )
    const signature = randomBase58(87)
    inference = {
      ...committed.inference,
      payment_signature: signature,
      cost_usdc: cost.toFixed(6),
      input_tokens: 3800 + Math.round(Math.random() * 900),
      output_tokens: 380 + Math.round(Math.random() * 220),
      latency_ms: 5200 + Math.round(Math.random() * 2600),
      devnet_ops_balance: ops ? ops.usdc_balance : committed.inference.devnet_ops_balance,
      sandbox_balance_before: sandboxBefore.toFixed(6),
      sandbox_balance_after: Math.max(0, sandboxBefore - cost).toFixed(6),
      projected_ops_balance: ops
        ? Math.max(0, n(ops.usdc_balance) - cost).toFixed(6)
        : committed.inference.projected_ops_balance,
    }
    newTx.push({
      time: nowIso,
      type: 'X402_INFERENCE',
      network: committed.inference.payment_network ?? 'surfpool-localnet',
      from_label: 'pay.sh Sandbox Wallet',
      to_label: 'TreAgent x402 Gateway',
      amount_usdc: cost.toFixed(6),
      status: 'CONFIRMED',
      signature,
      explorer_url: null,
    })
  }

  // 2. Execute the actions PolicyGuard approved or clamped for NOW. SCHEDULED
  // actions (e.g. vendor) stay untouched — their PENDING row already reads as
  // "queued" in the transactions table.
  let executedTotal = 0
  for (const action of committed.plan.actions) {
    if (action.timing !== 'NOW') continue
    const guard = committed.guard.find((g) => g.action_id === action.action_id)
    if (!guard || guard.verdict === 'REJECTED' || guard.verdict === 'HELD') continue
    const from = action.from_wallet_id ? wallets.get(action.from_wallet_id) : undefined
    const to = action.to_wallet_id ? wallets.get(action.to_wallet_id) : undefined
    if (!from || !to) continue
    const amount = n(guard.final_amount)
    if (amount <= 0 || n(from.usdc_balance) < amount) continue // already funded from a prior cycle
    from.usdc_balance = (n(from.usdc_balance) - amount).toFixed(6)
    to.usdc_balance = (n(to.usdc_balance) + amount).toFixed(6)
    executedTotal += amount
    newTx.push({
      time: nowIso,
      type: 'TRANSFER',
      network: 'solana-devnet',
      from_label: from.label,
      to_label: to.label,
      amount_usdc: amount.toFixed(6),
      status: 'CONFIRMED',
      // No real devnet signature: the execution stub never signs a tx (see
      // services/executor.py NotImplementedError). Keep this honest rather
      // than fabricating a signature that looks verifiable.
      signature: null,
      explorer_url: null,
    })
  }

  const recomputedWallets = [...wallets.values()].map(recomputeWallet)
  const totalTreasury = recomputedWallets.reduce((s, w) => s + n(w.usdc_balance), 0)
  const atRisk = recomputedWallets.filter((w) => n(w.shortfall) > 0)

  return {
    ...committed,
    generated_at: nowIso,
    decision_id: `dec_${randomHex(16)}`,
    context_hash: randomHex(66),
    wallets: recomputedWallets,
    inference,
    kpi: {
      ...committed.kpi,
      total_treasury_usdc: totalTreasury.toFixed(6),
      daily_spent_usdc: (n(committed.kpi.daily_spent_usdc) + executedTotal).toFixed(6),
      at_risk_count: atRisk.length,
      at_risk_usdc: atRisk.reduce((s, w) => s + n(w.shortfall), 0).toFixed(6),
    },
    transactions: [...newTx, ...committed.transactions],
  }
}

/**
 * Progressively overlays `pending` (this cycle's precomputed result) onto
 * `committed` (the persisted state) as the run advances, so each card's
 * numbers change exactly when its stage says they should:
 *  - decision id / context hash: as soon as a cycle starts (FORECASTING)
 *  - x402 payment + Operations Wallet balance: at PAYING
 *  - wallets / kpi / transactions: once the cycle finishes (DONE)
 */
export function displaySnapshot(
  committed: Snapshot,
  pending: Snapshot | null,
  stage: Stage,
): Snapshot {
  if (!pending) return committed
  let out = committed

  if (reached(stage, 'FORECASTING')) {
    out = { ...out, decision_id: pending.decision_id, context_hash: pending.context_hash }
  }

  if (reached(stage, 'PAYING')) {
    const opsPending = pending.wallets.find((w) => w.wallet_id === 'operations')
    out = {
      ...out,
      inference: pending.inference,
      wallets: out.wallets.map((w) =>
        w.wallet_id === 'operations' && opsPending
          ? { ...w, usdc_balance: opsPending.usdc_balance }
          : w,
      ),
    }
  }

  if (stage === 'DONE') {
    out = {
      ...out,
      wallets: pending.wallets,
      kpi: pending.kpi,
      transactions: pending.transactions,
      generated_at: pending.generated_at,
    }
  }

  return out
}
