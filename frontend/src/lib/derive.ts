/**
 * Vocabulary normalisation + snapshot-derived flags.
 *
 * The backend and the UI spec use slightly different words for the same states
 * (HEALTHY/SAFE, SHORTFALL/AT_RISK, CLAMPED/CLAMP). Every mapping lives here so
 * components never string-match on their own.
 */
import type { GuardCheck, Inference, Snapshot, TxRow, Wallet } from '../types'

export type WalletTone = 'SAFE' | 'WARNING' | 'AT_RISK'

export function walletTone(w: Wallet): WalletTone {
  const s = w.status.toUpperCase()
  if (s === 'AT_RISK' || s === 'SHORTFALL' || s === 'CRITICAL') return 'AT_RISK'
  if (s === 'WARNING' || s === 'LOW' || s === 'BUFFER_BREACH') return 'WARNING'
  return 'SAFE'
}

export const WALLET_TONE_LABEL: Record<WalletTone, string> = {
  SAFE: 'SAFE',
  WARNING: 'WARNING',
  AT_RISK: 'AT RISK',
}

export type CheckTone = 'PASS' | 'CLAMP' | 'FAIL' | 'SKIP'

export function checkTone(c: GuardCheck): CheckTone {
  const r = String(c.result).toUpperCase()
  if (r.startsWith('CLAMP')) return 'CLAMP'
  if (r === 'FAIL' || r === 'REJECT' || r === 'REJECTED' || r === 'BLOCK') return 'FAIL'
  if (r === 'SKIP' || r === 'SKIPPED' || r === 'N/A') return 'SKIP'
  return 'PASS'
}

export const CHECK_GLYPH: Record<CheckTone, string> = {
  PASS: '✓',
  CLAMP: '⚠',
  FAIL: '✕',
  SKIP: '–',
}

/**
 * A row is linkable iff it carries an explorer URL. Never inspect `network`:
 * flipping sandbox → mainnet must not require a frontend change.
 */
export function isLinkable(row: { explorer_url: string | null }): boolean {
  return Boolean(row.explorer_url)
}

/** Same rule for the inference card's signature. */
export function inferenceLinkable(inf: Inference): boolean {
  return Boolean(inf.explorer_url) && inf.signature_verifiable
}

export type ProviderMode = 'LIVE' | 'DEGRADED' | 'MOCK'

export function providerMode(inf: Inference): ProviderMode {
  if (inf.provider === 'mock') return 'MOCK'
  if (inf.degraded) return 'DEGRADED'
  return 'LIVE'
}

/** True when the x402 challenge → payment → response round-trip actually ran. */
export function hasPaymentFlow(inf: Inference): boolean {
  return inf.challenge_status !== null && inf.response_status !== null
}

/**
 * Prompt injection is reported either through `alerts` or, when the backend
 * only escalates, through an escalation reason. Both paths raise the banner.
 */
export function injectionDetected(snap: Snapshot): boolean {
  if (snap.alerts.some((a) => a.toUpperCase().includes('INJECTION'))) return true
  return Boolean(snap.plan.escalation_required && snap.plan.escalation_reason_ko)
}

/**
 * The offending memo is quoted inside `escalation_reason_ko`. Pull the quoted
 * span out rather than hardcoding attack text in the UI.
 */
export function extractInjectedMemo(reason: string | null): string | null {
  if (!reason) return null
  const m = reason.match(/['"“'‘]([^'"”'’]{12,})['"”'’]/)
  return m ? m[1].trim() : null
}

/** Wallet ids the guard is willing to touch — used by the security panel. */
export function whitelistedWalletIds(snap: Snapshot): string[] {
  return snap.wallets.map((w) => w.wallet_id)
}

/**
 * Wallet ids the plan actually routes to, to prove no attacker address
 * slipped in. Actions PolicyGuard rejected never route anywhere, so they're
 * excluded — otherwise a blocked attacker_wallet action would wrongly count
 * as "planned".
 */
export function plannedWalletIds(snap: Snapshot): string[] {
  const ids = new Set<string>()
  for (const a of snap.plan.actions) {
    if (guardFor(snap, a.action_id)?.verdict === 'REJECTED') continue
    if (a.from_wallet_id) ids.add(a.from_wallet_id)
    if (a.to_wallet_id) ids.add(a.to_wallet_id)
  }
  return [...ids]
}

export function guardFor(snap: Snapshot, actionId: string) {
  return snap.guard.find((g) => g.action_id === actionId)
}

export function walletById(snap: Snapshot, id: string | null) {
  if (!id) return undefined
  return snap.wallets.find((w) => w.wallet_id === id)
}

export function walletByAddress(snap: Snapshot, address: string) {
  return snap.wallets.find((w) => w.address === address)
}

/** X402 rows get the AI accent — inference payment is a transaction too. */
export function isInferenceTx(row: TxRow): boolean {
  return row.type.toUpperCase().includes('X402')
}

export function daily(snap: Snapshot): { spent: number; limit: number; pct: number } {
  const spent = Number(snap.kpi.daily_spent_usdc) || 0
  const limit = Number(snap.kpi.daily_limit_usdc) || 0
  const pct = limit > 0 ? Math.min(100, (spent / limit) * 100) : 0
  return { spent, limit, pct }
}
