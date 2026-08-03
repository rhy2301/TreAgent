#!/usr/bin/env python3
"""시나리오 하나를 끝까지 돌리고 터미널 + JSON 스냅샷으로 낸다.

    python -m scripts.demo --scenario normal_shortfall --provider paysh
    python -m scripts.demo --scenario prompt_injection --provider aistudio
    python -m scripts.demo --scenario inference_budget_low --provider mock \\
        --emit-json ../frontend/public/snapshot.inference_budget_low.json

터미널 출력이 곧 제출 영상이고, ``--emit-json`` 산출물이 곧 프론트의 입력이다.
둘은 같은 :class:`Snapshot` 에서 나온다 — 화면과 영상이 어긋날 수 없게 하려는
것이다.

네트워크 분리
-------------
``treasury`` 는 Solana **Devnet**, ``inference`` 는 pay.sh **Surfpool localnet**
이다. 별개 네트워크이므로 x402 결제가 Devnet Operations Wallet 에서 빠져나간
것처럼 표시하지 않는다. localnet 서명에는 익스플로러 링크를 만들지 않는다 —
공개 익스플로러는 localnet 을 모르고, 링크를 걸면 *다른 체인의 엉뚱한
트랜잭션* 을 가리킬 수 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from treagent.judgment import forecaster, inference_cost  # noqa: E402
from treagent.judgment.engine import CycleOutcome, JudgmentEngine  # noqa: E402
from treagent.judgment.gating import GatingState  # noqa: E402
from treagent.judgment.guard import (  # noqa: E402
    LOW_CONFIDENCE_THRESHOLD,
    CollectingAlertHook,
    PolicyGuard,
)
from treagent.judgment.models import (  # noqa: E402
    ZERO,
    DecisionContext,
    ExecutionIntent,
    Forecast,
    coerce_usdc,
)
from treagent.judgment.providers.base import LLMProvider, ProviderResult  # noqa: E402

SCENARIOS = ("normal_shortfall", "prompt_injection", "inference_budget_low")
PROVIDERS = ("paysh", "aistudio", "mock")

SCENARIO_DIR = BACKEND_DIR / "tests" / "fixtures" / "scenarios"

WALLET_LABELS = {
    "vault": "Treasury Vault",
    "operations": "Operations Wallet",
    "payroll": "Payroll Wallet",
    "vendor": "Vendor Wallet",
}

DEVNET_EXPLORER = "https://explorer.solana.com/tx/{signature}?cluster=devnet"
"""**Devnet 자금 집행 전용.** localnet x402 서명에는 절대 쓰지 않는다."""

# --------------------------------------------------------------------------- #
# 터미널 서식 — 이모지 대신 정렬과 구분선
# --------------------------------------------------------------------------- #

WIDTH = 84
_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(text: str) -> str:
    return _c("1", text)


def red(text: str) -> str:
    return _c("31", text)


def green(text: str) -> str:
    return _c("32", text)


def yellow(text: str) -> str:
    return _c("33", text)


def cyan(text: str) -> str:
    return _c("36", text)


def dim(text: str) -> str:
    return _c("2", text)


def rule(char: str = "=") -> str:
    return char * WIDTH


def section(title: str) -> None:
    print()
    print(bold(rule()))
    print(bold(f"  {title}"))
    print(bold(rule()))


def amount(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}"


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


# --------------------------------------------------------------------------- #
# 시나리오 로딩
# --------------------------------------------------------------------------- #


def load_scenario(name: str) -> tuple[DecisionContext, datetime, Decimal | None, dict[str, Any]]:
    raw = json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))
    budget = raw.get("inference_budget_days")
    return (
        DecisionContext.model_validate(raw["context"]),
        datetime.fromisoformat(raw["now"]),
        None if budget is None else coerce_usdc(budget),
        raw,
    )


class SnapshotBalances:
    """가드 8번(서명 직전 잔고 재조회)의 데모용 공급자.

    실제 어댑터는 ``chain.transfer`` 로 RPC 를 다시 친다. 데모는 시나리오
    스냅샷을 그대로 쓴다 — 재현 가능해야 영상이 매번 같은 그림을 낸다.
    """

    def __init__(self, context: DecisionContext) -> None:
        self._balances = {w.address: w.usdc_balance for w in context.wallets}

    async def get_usdc_balance(self, address: str) -> Decimal:
        return self._balances.get(address, ZERO)


def build_provider(
    name: str, scenario: str, context: DecisionContext
) -> tuple[LLMProvider | None, str | None]:
    """공급자를 만든다. 만들지 못하면 ``(None, 사유)``."""
    if name == "mock":
        from treagent.judgment.providers.mock import MockProvider

        return MockProvider(scenario=scenario), None

    if name == "aistudio":
        from treagent.judgment.providers.aistudio import AIStudioGeminiProvider

        if not os.getenv("GEMINI_API_KEY"):
            return None, "GEMINI_API_KEY 가 설정되지 않았다."
        return AIStudioGeminiProvider(), None

    from treagent.judgment.providers.paysh import PayShGeminiProvider

    ops = context.wallet(inference_cost.OPERATIONS_WALLET_ID)
    return (
        PayShGeminiProvider(
            inference_budget_min_days=context.policy.inference_budget_min_days,
            devnet_ops_balance=ops.usdc_balance if ops else None,
        ),
        None,
    )


# --------------------------------------------------------------------------- #
# 가드 검사 표 — GuardResult 를 화면에 낼 수 있는 형태로 편다
# --------------------------------------------------------------------------- #

CHECKS: tuple[tuple[str, str, str], ...] = (
    ("1", "SCHEMA", "스키마 파싱"),
    ("2", "WHITELIST", "wallet_id 화이트리스트"),
    ("3", "FUNDING_OPTION", "funding_option 실재·대상 일치"),
    ("4", "TREASURY_CONSISTENCY", "트레저리 일관성"),
    ("5", "PER_TRANSFER_LIMIT", "1회 이체 한도"),
    ("6", "DAILY_LIMIT", "일일 누적 한도"),
    ("7", "SLIPPAGE", "슬리피지 한도"),
    ("8", "LIVE_BALANCE", "서명 직전 잔고 재조회"),
    ("9", "IDEMPOTENCY", "intent_id 24h 멱등"),
    ("10", "CONFIDENCE", "confidence 하한"),
)

FUNDING_TYPES = frozenset({"TRANSFER", "SWAP_THEN_TRANSFER"})


def _notes_for(action_id: str, notes: list[str]) -> list[str]:
    return [note for note in notes if f"] {action_id}:" in note or action_id in note]


def _check_number(note: str) -> str | None:
    """``[5] ...`` 에서 ``5`` 를 뽑는다. 가드가 모든 메모에 검사 번호를 붙인다."""
    if not note.startswith("["):
        return None
    return note[1 : note.find("]")] if "]" in note else None


def build_guard_rows(
    outcome: CycleOutcome, forecast: Forecast
) -> list[dict[str, Any]]:
    """액션마다 10개 검사의 통과 여부를 편다.

    가드는 검사 결과를 구조체로 내놓지 않고 ``[N] ...`` 접두사가 붙은 메모로
    낸다. 그 접두사가 사실상의 계약이라 여기서 되짚을 수 있다. 가드가 메모를
    남기지 않은 검사는 통과한 것이다 — 가드는 문제가 있을 때만 기록한다.
    """
    plan_rejected = outcome.guard.verdict == "REJECTED"
    plan_held = outcome.guard.held
    # ExecutionIntent 는 hashable 이 아니다 (guard_notes 가 list). id 로 추적한다.
    used: set[str] = set()

    rows: list[dict[str, Any]] = []
    for action in outcome.plan.actions:
        option = (
            forecast.option(action.funding_option_id)
            if action.funding_option_id
            else None
        )
        requested = option.amount_usdc if option else None

        action_notes = _notes_for(action.action_id, outcome.guard.notes)
        dropped = action.action_id in outcome.guard.dropped_action_ids

        intent: ExecutionIntent | None = None
        if requested is not None and not dropped:
            # 금액이 그대로면 그 인텐트다. 클램프되면 금액이 바뀌므로 아직 쓰지
            # 않은 것 중 앞의 것을 집는다 (가드는 priority 순으로 발행한다).
            intent = next(
                (
                    candidate
                    for candidate in outcome.intents
                    if candidate.intent_id not in used
                    and candidate.amount_usdc == requested
                ),
                None,
            ) or next(
                (
                    candidate
                    for candidate in outcome.intents
                    if candidate.intent_id not in used
                ),
                None,
            )
        if intent is not None:
            used.add(intent.intent_id)

        failing = {
            _check_number(note): note
            for note in action_notes
            if _check_number(note) is not None
        }
        clamped_notes = {
            _check_number(note): note
            for note in (intent.guard_notes if intent else [])
            if _check_number(note) is not None
        }

        checks: list[dict[str, Any]] = []
        for number, check_id, label in CHECKS:
            result = "PASS"
            note: str | None = None
            if action.type not in FUNDING_TYPES and number in {"3", "5", "6", "7", "8", "9"}:
                # 자금을 움직이지 않는 액션은 금액 관련 검사를 타지 않는다.
                result = "SKIP"
                note = "자금 이동이 없는 액션이다."
            elif plan_rejected and number in {"2", "4"}:
                # 2·4 는 전체 거부 검사다. 사유 메모는 계획 단위로 남는다.
                blocking = [n for n in outcome.guard.notes if n.startswith(f"[{number}]")]
                if blocking:
                    result, note = "FAIL", blocking[0]
            elif number in failing:
                result, note = "DROPPED", failing[number]
            elif number in clamped_notes:
                result, note = "CLAMPED", clamped_notes[number]
            elif number == "10" and plan_held:
                result = "FAIL"
                note = (
                    f"confidence {outcome.plan.confidence} < {LOW_CONFIDENCE_THRESHOLD}"
                )
            checks.append(
                {"id": check_id, "label": label, "result": result, "note": note}
            )

        if plan_rejected:
            verdict = "REJECTED"
        elif dropped:
            verdict = "DROPPED"
        elif plan_held:
            verdict = "HELD"
        elif intent is not None:
            verdict = intent.guard_verdict
        elif action.type in FUNDING_TYPES:
            verdict = "DROPPED"
        else:
            verdict = "NO_INTENT"

        rows.append(
            {
                "action_id": action.action_id,
                "verdict": verdict,
                "requested_amount": _dec(requested),
                "final_amount": _dec(intent.amount_usdc if intent else None),
                "checks": checks,
                "notes": action_notes + (list(intent.guard_notes) if intent else []),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 스냅샷
# --------------------------------------------------------------------------- #


def wallet_status(shortfall: Decimal, balance: Decimal, buffer: Decimal) -> str:
    if shortfall > ZERO:
        return "SHORTFALL"
    if balance < buffer:
        return "BELOW_BUFFER"
    return "HEALTHY"


def build_snapshot(
    *,
    scenario: str,
    provider_name: str,
    outcome: CycleOutcome,
    context: DecisionContext,
    now: datetime,
    provider_result: ProviderResult | None,
    inference_meta: dict[str, Any],
    booked_obligation_id: str | None,
    alerts: list[str],
) -> dict[str, Any]:
    forecast = outcome.forecast
    policy = context.policy
    horizon = forecaster.obligations_in_horizon(
        context.obligations, now, policy.forecast_horizon_hours
    )
    shortfall_wallets = {
        s.wallet_id for s in forecast.shortfalls if s.shortfall > ZERO
    }
    at_risk_obligations = [o for o in horizon if o.wallet_id in shortfall_wallets]

    wallets: list[dict[str, Any]] = []
    for state in sorted(context.wallets, key=lambda w: w.wallet_id):
        shortfall = forecast.shortfall_for(state.wallet_id)
        short_amount = shortfall.shortfall if shortfall else ZERO
        wallets.append(
            {
                "wallet_id": state.wallet_id,
                "label": WALLET_LABELS.get(state.wallet_id, state.wallet_id),
                "address": state.address,
                "usdc_balance": _dec(state.usdc_balance),
                "required_outflow": _dec(
                    shortfall.required_outflow if shortfall else ZERO
                ),
                "min_buffer_usdc": _dec(state.min_buffer_usdc),
                "shortfall": _dec(short_amount),
                "status": wallet_status(
                    short_amount, state.usdc_balance, state.min_buffer_usdc
                ),
                "earliest_due_at": _iso(shortfall.earliest_due_at if shortfall else None),
            }
        )

    intents = [
        {
            "intent_id": intent.intent_id,
            "from_address": intent.from_address,
            "to_address": intent.to_address,
            "amount_usdc": _dec(intent.amount_usdc),
            "guard_verdict": intent.guard_verdict,
        }
        for intent in outcome.intents
    ]

    return {
        "generated_at": now.isoformat(),
        "agent_status": _agent_status(outcome),
        "decision_id": outcome.decision_id,
        "context_hash": outcome.context_hash,
        "scenario": scenario,
        "networks": {
            "treasury": {"label": "Solana Devnet", "status": "CONNECTED"},
            "inference": {
                "label": "pay.sh Sandbox (Surfpool localnet)",
                "status": (
                    "CONNECTED"
                    if inference_meta.get("payment_signature")
                    else "NOT_USED"
                ),
            },
        },
        "kpi": {
            "total_treasury_usdc": _dec(
                sum((w.usdc_balance for w in context.wallets), ZERO)
            ),
            "scheduled_payments_usdc": _dec(
                sum((o.amount_usdc for o in horizon), ZERO)
            ),
            "scheduled_payments_count": len(horizon),
            "at_risk_count": len(at_risk_obligations),
            "at_risk_usdc": _dec(
                sum((o.amount_usdc for o in at_risk_obligations), ZERO)
            ),
            "daily_limit_usdc": _dec(policy.max_daily_total_usdc),
            "daily_spent_usdc": _dec(policy.daily_spent_usdc),
        },
        "wallets": wallets,
        "inference": {**inference_meta, "booked_as_obligation_id": booked_obligation_id},
        "plan": outcome.plan.model_dump(mode="json"),
        "guard": build_guard_rows(outcome, forecast),
        "alerts": alerts,
        "intents": intents,
        "transactions": build_transactions(outcome, context, now, inference_meta),
        # compare.py 가 아직 없다. 프론트는 null 이면 해당 패널을 숨긴다.
        "comparison": None,
    }


def _agent_status(outcome: CycleOutcome) -> str:
    if outcome.guard.held:
        return "HELD"
    if outcome.guard.rejected:
        return "REJECTED"
    if outcome.plan.escalation_required:
        return "ESCALATED"
    if outcome.intents:
        return "ACTIVE"
    return "IDLE"


def build_transactions(
    outcome: CycleOutcome,
    context: DecisionContext,
    now: datetime,
    inference_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """집행 대상과 x402 결제를 한 표에 담는다. **네트워크는 섞지 않는다.**"""
    by_address = {w.address: w for w in context.wallets}
    rows: list[dict[str, Any]] = []

    signature = inference_meta.get("payment_signature")
    if signature:
        rows.append(
            {
                "time": now.isoformat(),
                "type": "X402_INFERENCE",
                "network": "surfpool-localnet",
                "from_label": "pay.sh Sandbox Wallet",
                "to_label": "TreAgent x402 Gateway",
                "amount_usdc": inference_meta.get("cost_usdc"),
                "status": "CONFIRMED",
                "signature": signature,
                # localnet 서명에는 익스플로러 링크를 만들지 않는다.
                "explorer_url": None,
            }
        )

    for intent in outcome.intents:
        source = by_address.get(intent.from_address)
        target = by_address.get(intent.to_address)
        rows.append(
            {
                "time": intent.execute_after.isoformat(),
                "type": "TRANSFER",
                "network": "solana-devnet",
                "from_label": WALLET_LABELS.get(
                    source.wallet_id if source else "", intent.from_address[:8]
                ),
                "to_label": WALLET_LABELS.get(
                    target.wallet_id if target else "", intent.to_address[:8]
                ),
                "amount_usdc": _dec(intent.amount_usdc),
                # 아직 서명하지 않았다. 집행기가 붙으면 CONFIRMED + 서명이 채워진다.
                "status": "PENDING",
                "signature": None,
                "explorer_url": None,
            }
        )
    return rows


def devnet_explorer_url(signature: str | None) -> str | None:
    """Devnet 자금 집행 전용 링크. x402 localnet 서명에는 쓰지 않는다."""
    return DEVNET_EXPLORER.format(signature=signature) if signature else None


# --------------------------------------------------------------------------- #
# 터미널 출력
# --------------------------------------------------------------------------- #


def render(snapshot: dict[str, Any], provider_name: str, skip_reason: str | None) -> None:
    section(f"TreAgent 판단 사이클 — 시나리오: {snapshot['scenario']}")
    print(f"  decision_id  : {snapshot['decision_id']}")
    print(f"  context_hash : {dim(snapshot['context_hash'][:32] + '…')}")
    print(f"  agent_status : {bold(snapshot['agent_status'])}")
    networks = snapshot["networks"]
    print(f"  treasury     : {networks['treasury']['label']}  [{networks['treasury']['status']}]")
    print(f"  inference    : {networks['inference']['label']}  [{networks['inference']['status']}]")

    section("1. 초기 지갑 상태 + Forecaster 계산 (Python 이 계산한 숫자다)")
    print(
        f"  {'wallet':<12}{'balance':>12}{'required':>12}{'buffer':>10}"
        f"{'shortfall':>12}  status"
    )
    print("  " + "-" * (WIDTH - 4))
    for wallet in snapshot["wallets"]:
        short = Decimal(wallet["shortfall"])
        line = (
            f"  {wallet['wallet_id']:<12}"
            f"{amount(Decimal(wallet['usdc_balance'])):>12}"
            f"{amount(Decimal(wallet['required_outflow'])):>12}"
            f"{amount(Decimal(wallet['min_buffer_usdc'])):>10}"
            f"{amount(short):>12}  {wallet['status']}"
        )
        print(red(line) if short > ZERO else line)
    kpi = snapshot["kpi"]
    print("  " + "-" * (WIDTH - 4))
    print(
        f"  예정 지출 {kpi['scheduled_payments_count']}건 "
        f"{amount(Decimal(kpi['scheduled_payments_usdc']))} USDC  |  "
        f"위험 {kpi['at_risk_count']}건 {amount(Decimal(kpi['at_risk_usdc']))} USDC  |  "
        f"일일 한도 {amount(Decimal(kpi['daily_spent_usdc']))}/"
        f"{amount(Decimal(kpi['daily_limit_usdc']))}"
    )

    section("2. x402 추론 결제 (pay.sh Surfpool localnet — Devnet 과 별개다)")
    inference = snapshot["inference"]
    if skip_reason:
        print(f"  {yellow('건너뜀')} — {skip_reason}")
    if inference.get("payment_signature"):
        print(f"  {red('402 Payment Required')}   {inference['gateway_url']}")
        print(f"       ↓  {inference['cost_usdc']} USDC  ({inference['payment_network']})")
        print(f"  {cyan('결제 서명')} {inference['payment_signature']}")
        print("       ↓")
        print(f"  {green('200 OK')}   {inference['model']}  "
              f"in={inference['input_tokens']} out={inference['output_tokens']} "
              f"{(inference['latency_ms'] or 0) / 1000:.1f}s")
        print(
            dim(
                f"  검증가능 서명: {inference['signature_verifiable']}  "
                f"explorer_url: {inference['explorer_url']}  "
                "(localnet 은 공개 익스플로러에 없다)"
            )
        )
    else:
        print(f"  결제 없음 — provider={provider_name} "
              f"degraded={inference.get('degraded')} "
              f"reason={inference.get('degraded_reason')}")

    print()
    print(f"  {'실측 청구액':<22}: {inference.get('cost_usdc')} USDC  (이번 1회)")
    print(f"  {'가정 단가':<22}: {inference.get('assumed_unit_cost_usdc')} USDC/회  "
          f"x 일 {inference.get('projected_daily_calls')}회  {dim('← 예산 계산용 가정치')}")
    print(f"  {'sandbox 잔고':<22}: {inference.get('sandbox_balance_before')} → "
          f"{inference.get('sandbox_balance_after')}")
    print(f"  {'추론 여유':<22}: {inference.get('estimated_runway_days')}일")
    print(f"  {'Devnet Ops 잔고':<22}: {inference.get('devnet_ops_balance')} "
          f"{dim('(x402 로 차감되지 않는다 — 다른 네트워크다)')}")
    print(f"  {'기표된 obligation':<22}: {inference.get('booked_as_obligation_id')}")

    section("3. 판단 결과")
    plan = snapshot["plan"]
    print(f"  overall_risk         : {bold(plan['overall_risk'])}")
    print(f"  confidence           : {plan['confidence']}")
    print(f"  escalation_required  : {plan['escalation_required']}")
    print(f"  {plan['reasoning_summary_ko']}")
    if plan.get("escalation_reason_ko"):
        print()
        print(yellow("  escalation_reason_ko:"))
        for line in _wrap(plan["escalation_reason_ko"], WIDTH - 6):
            print(yellow(f"    {line}"))
    for action in plan["actions"]:
        print()
        print(f"  [{action['priority']}] {bold(action['action_id'])} "
              f"{action['type']} {action['funding_option_id'] or ''} "
              f"{action['timing']}")
        for line in _wrap(action["rationale_ko"], WIDTH - 8):
            print(f"      {line}")

    section("4. PolicyGuard 판정")
    for row in snapshot["guard"]:
        verdict = row["verdict"]
        painted = {
            "APPROVED": green,
            "CLAMPED": yellow,
            "DROPPED": red,
            "REJECTED": red,
            "HELD": yellow,
        }.get(verdict, dim)(verdict)
        print(f"  {row['action_id']:<24} {painted:<22} "
              f"{row['requested_amount'] or '-'} → {row['final_amount'] or '-'}")
        for check in row["checks"]:
            if check["result"] in ("PASS", "SKIP"):
                continue
            mark = red if check["result"] in ("DROPPED", "FAIL") else yellow
            print(mark(f"      [{check['result']}] {check['label']}"))
            if check["note"]:
                for line in _wrap(check["note"], WIDTH - 12):
                    print(dim(f"        {line}"))
    if any(row["verdict"] == "CLAMPED" for row in snapshot["guard"]):
        print()
        print(yellow(bold("  ** CLAMPED — 한도에 걸려 금액이 절삭됐다. 집행은 진행된다. **")))
    for alert in snapshot["alerts"]:
        print(red(f"  ALERT  {alert}"))

    section("5. 집행 인텐트 / 트랜잭션")
    if not snapshot["intents"]:
        print(dim("  집행 대상 없음."))
    for tx in snapshot["transactions"]:
        print(f"  {tx['type']:<16} {tx['network']:<20} "
              f"{tx['from_label']} → {tx['to_label']}  "
              f"{tx['amount_usdc']} USDC  [{tx['status']}]")
        if tx["signature"]:
            print(dim(f"      sig: {tx['signature']}"))
        if tx["explorer_url"]:
            print(dim(f"      {tx['explorer_url']}"))
    print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TreAgent 판단 사이클 데모")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal_shortfall")
    parser.add_argument("--provider", choices=PROVIDERS, default="paysh")
    parser.add_argument("--emit-json", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_DIR.parent / ".env")
    except ImportError:  # pragma: no cover
        pass

    context, now, budget_days, _ = load_scenario(args.scenario)
    provider, provider_error = build_provider(args.provider, args.scenario, context)

    alert_hook = CollectingAlertHook()
    state = GatingState(inference_budget_days=budget_days)
    engine = JudgmentEngine(
        guard=PolicyGuard(balances=SnapshotBalances(context), alerts=alert_hook),
        state=state,
        provider=provider,
    )

    outcome = asyncio.run(engine.run_cycle(context, now))
    result = outcome.provider_result

    skip_reason = provider_error
    if outcome.gating.skipped:
        skip_reason = f"게이팅이 호출을 건너뛰었다 ({outcome.gating.skip_reason})."
    elif result is None and provider_error is None:
        skip_reason = "공급자 호출이 실패해 결정론적 폴백으로 완주했다."

    inference_meta = build_inference_meta(args.provider, result, skip_reason)

    next_context, booked = inference_cost.book_inference_cost(
        context, now, cost_usdc=result.cost_usdc if result else None
    )

    snapshot = build_snapshot(
        scenario=args.scenario,
        provider_name=args.provider,
        outcome=outcome,
        context=context,
        now=now,
        provider_result=result,
        inference_meta=inference_meta,
        booked_obligation_id=booked,
        alerts=[f"{a.kind}: {a.message}" for a in alert_hook.alerts],
    )

    render(snapshot, args.provider, skip_reason)

    if booked is not None:
        forecast_next = forecaster.build_forecast(next_context, now)
        ops = forecast_next.shortfall_for(inference_cost.OPERATIONS_WALLET_ID)
        if ops is not None:
            print(bold("  다음 사이클 반영 (재귀 루프)"))
            print(f"    {booked} 를 operations 에 기표했다.")
            print(
                f"    operations required_outflow: "
                f"{amount(outcome.forecast.shortfall_for('operations').required_outflow if outcome.forecast.shortfall_for('operations') else ZERO)}"
                f" → {amount(ops.required_outflow)} USDC"
            )
            print()

    if args.emit_json:
        args.emit_json.parent.mkdir(parents=True, exist_ok=True)
        args.emit_json.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(green(f"  스냅샷 기록: {args.emit_json}"))
        print()
    return 0


def build_inference_meta(
    provider_name: str, result: ProviderResult | None, skip_reason: str | None
) -> dict[str, Any]:
    """``ProviderResult`` 를 스냅샷의 ``inference`` 블록으로 옮긴다.

    paysh 는 이미 모든 필드를 ``metadata`` 에 담아 온다. 그 외 경로는 결제가
    없으므로 결제 관련 필드가 전부 ``None`` 이고, 그것이 정확한 표현이다.
    """
    from treagent.judgment.providers.aistudio import DEFAULT_MODEL

    base: dict[str, Any] = {
        "provider": provider_name,
        "model": DEFAULT_MODEL if provider_name != "mock" else "mock-fixture",
        "gateway_type": "self-hosted" if provider_name == "paysh" else None,
        "payment_mode": None,
        "payment_network": None,
        "gateway_url": None,
        "challenge_status": None,
        "response_status": None,
        "payment_signature": None,
        "signature_verifiable": False,
        "explorer_url": None,
        "cost_usdc": None,
        "assumed_unit_cost_usdc": _dec(inference_cost.assumed_unit_cost_usdc()),
        "projected_daily_calls": inference_cost.PROJECTED_DAILY_CALLS,
        "input_tokens": None,
        "output_tokens": None,
        "latency_ms": 0,
        "degraded": skip_reason is not None,
        "degraded_reason": skip_reason,
        "devnet_ops_balance": None,
        "sandbox_balance_before": None,
        "sandbox_balance_after": None,
        "projected_ops_balance": None,
        "estimated_runway_days": None,
    }
    if result is None:
        return base

    base.update(
        {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
            "payment_signature": result.payment_signature,
            "cost_usdc": _dec(result.cost_usdc),
            "degraded": result.degraded or skip_reason is not None,
        }
    )
    for key, value in result.metadata.items():
        if key in base and value is not None:
            base[key] = value
    if base["degraded"] and not base["degraded_reason"]:
        base["degraded_reason"] = result.metadata.get("degraded_reason")
    # localnet 서명에는 절대 익스플로러 URL 을 만들지 않는다 — 여기서 한 번 더 못박는다.
    if not base["signature_verifiable"]:
        base["explorer_url"] = None
    return base


if __name__ == "__main__":
    raise SystemExit(main())
