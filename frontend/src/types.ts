/**
 * Snapshot contract — the only input this dashboard has.
 * Produced by `scripts/demo.py --scenario <name> --emit-json <path>`.
 *
 * Amounts arrive as decimal *strings* (never floats) and are rendered as-is
 * after formatting; nothing here is recomputed on the client.
 */

export type NetworkStatus = string
export type AgentStatus = 'ACTIVE' | 'PAUSED' | 'ESCALATED' | 'HELD' | 'REJECTED' | 'IDLE'

export interface NetworkInfo {
  label: string
  status: NetworkStatus
}

export interface Networks {
  /** Fund execution layer — Solana Devnet. */
  treasury: NetworkInfo
  /** Payment protocol layer — pay.sh sandbox. A *different* network. */
  inference: NetworkInfo
}

export interface Kpi {
  total_treasury_usdc: string
  scheduled_payments_usdc: string
  scheduled_payments_count: number
  at_risk_count: number
  at_risk_usdc: string
  daily_limit_usdc: string
  daily_spent_usdc: string
}

/** WalletState + WalletShortfall, merged. All of these live on Solana Devnet. */
export interface Wallet {
  wallet_id: string
  label: string
  address: string
  usdc_balance: string
  required_outflow: string
  min_buffer_usdc: string
  shortfall: string
  /** Backend emits HEALTHY | WARNING | SHORTFALL; SAFE/AT_RISK accepted too. */
  status: string
  earliest_due_at: string | null
}

/** x402 inference payment. Note: not the same network as `wallets`. */
export interface Inference {
  provider: 'paysh' | 'aistudio' | 'mock' | string
  model: string | null
  gateway_type?: string | null
  payment_mode: string | null
  payment_network: string | null
  gateway_url: string | null

  challenge_status: number | null
  response_status: number | null
  payment_signature: string | null
  /** Only when true may a signature be linked out. */
  signature_verifiable: boolean
  /** Sole source of truth for "is this on a public explorer". */
  explorer_url: string | null
  cost_usdc: string | null
  assumed_unit_cost_usdc?: string | null
  projected_daily_calls?: number | null

  input_tokens: number | null
  output_tokens: number | null
  latency_ms: number | null
  degraded: boolean
  degraded_reason: string | null

  devnet_ops_balance: string | null
  sandbox_balance_before: string | null
  sandbox_balance_after: string | null
  /** Projection, not an on-chain balance. Always tooltipped as such. */
  projected_ops_balance: string | null
  estimated_runway_days: string | number | null
  /** The recursion: inference cost booked back as an obligation. */
  booked_as_obligation_id: string | null
}

export interface AtRiskObligation {
  obligation_id: string
  severity: string
  reason_ko: string
}

export type ActionType = 'TRANSFER' | 'SWAP_THEN_TRANSFER' | 'NOOP' | 'ESCALATE' | string

export interface ProposedAction {
  action_id: string
  type: ActionType
  funding_option_id: string | null
  from_wallet_id: string | null
  to_wallet_id: string | null
  source_token?: string | null
  max_slippage_bps?: number | null
  timing: 'NOW' | 'SCHEDULED' | string
  scheduled_for: string | null
  priority: number
  rationale_ko: string
}

export interface DecisionPlan {
  overall_risk: string
  confidence: number
  reasoning_summary_ko: string
  escalation_required: boolean
  escalation_reason_ko: string | null
  at_risk: AtRiskObligation[]
  actions: ProposedAction[]
}

export type CheckResult = 'PASS' | 'CLAMP' | 'CLAMPED' | 'FAIL' | 'REJECT' | 'SKIP' | string

export interface GuardCheck {
  id: string | number
  label: string
  result: CheckResult
  note?: string | null
}

export type GuardVerdict = 'APPROVED' | 'CLAMPED' | 'REJECTED' | 'HELD' | string

export interface GuardRow {
  action_id: string
  verdict: GuardVerdict
  requested_amount: string
  final_amount: string
  checks: GuardCheck[]
  notes: string[]
}

export interface Comparison {
  action_count_delta: number
  total_amount_delta: string
  timing_differences: string[]
  priority_differences: string[]
  llm_only_insights: string[]
  fallback_summary_ko: string
  llm_summary_ko: string
}

export interface ExecutionIntent {
  intent_id: string
  from_address: string
  to_address: string
  amount_usdc: string
  guard_verdict: GuardVerdict
}

export interface TxRow {
  time: string
  type: string
  network: string
  from_label: string
  to_label: string
  amount_usdc: string
  status: 'CONFIRMED' | 'PENDING' | 'FAILED' | string
  signature: string | null
  /** Present ⇒ linkable. Absent ⇒ proof only. Never branch on `network`. */
  explorer_url: string | null
}

export interface Snapshot {
  generated_at: string
  agent_status: AgentStatus
  decision_id: string
  context_hash: string
  scenario: string
  networks: Networks
  kpi: Kpi
  wallets: Wallet[]
  inference: Inference
  plan: DecisionPlan
  guard: GuardRow[]
  alerts: string[]
  comparison: Comparison | null
  intents: ExecutionIntent[]
  transactions: TxRow[]
}

export const SCENARIOS = [
  'normal_shortfall',
  'prompt_injection',
  'inference_budget_low',
] as const

export type ScenarioName = (typeof SCENARIOS)[number]
