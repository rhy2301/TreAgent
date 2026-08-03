"""결정론적 예측과 폴백 계획.

이 모듈이 산수의 전부다. LLM 은 여기서 나온 :class:`FundingOption` 중 하나를
``option_id`` 로 고를 수만 있고, 금액을 스스로 만들어낼 수 없다.

:func:`build_fallback_plan` 이 :class:`DecisionPlan` 을 반환하는 것이 핵심이다.
Mock·Fallback·Gemini 가 모두 같은 타입을 내놓으므로 같은 가드를 통과한다.
LLM 이 완전히 죽어도 파이프라인의 뒷단은 달라지지 않는다 (원칙 4).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from treagent.judgment.models import (
    ZERO,
    AtRiskObligation,
    DecisionContext,
    DecisionPlan,
    Forecast,
    FundingOption,
    Obligation,
    Policy,
    ProposedAction,
    RiskLevel,
    WalletShortfall,
    quantize_down,
    quantize_up,
)

TREASURY_WALLET_ID = "vault"
"""자금 공급원. 이 지갑은 이체 **대상**이 되지 않는다 (자기 자신에게 못 보낸다)."""

IMMINENT_WINDOW = timedelta(hours=24)
"""이 안에 만기가 오면 지금 집행한다."""

CATEGORY_RANK: dict[str, int] = {
    "PAYROLL": 0,
    "VENDOR": 1,
    "API_USAGE": 2,
    "OTHER": 3,
}
"""정렬 우선순위. 숫자가 작을수록 먼저다.

급여를 맨 앞에 두는 것은 임의 선택이 아니다. 미지급 시 법적 의무 위반이고
되돌릴 수 없다. 벤더 대금은 연체 이자로, API 요금은 서비스 중단으로 끝난다.
"""

_LOWEST_RANK = max(CATEGORY_RANK.values()) + 1


# --------------------------------------------------------------------------- #
# 예측
# --------------------------------------------------------------------------- #


def obligations_in_horizon(
    obligations: list[Obligation], now: datetime, horizon_hours: int
) -> list[Obligation]:
    """예측 구간 안에 만기가 있는 지출을 고른다.

    하한을 두지 않는 것이 의도적이다. ``due_at`` 이 이미 지난 지출은 여전히
    갚아야 할 돈이므로 요구 유출액에 포함되어야 한다. 하한을 ``now`` 로 두면
    연체된 급여가 예측에서 사라져 부족분이 과소평가된다.
    """
    deadline = now + timedelta(hours=horizon_hours)
    return [o for o in obligations if o.due_at <= deadline]


def _required_outflow(obligations: list[Obligation]) -> Decimal:
    """요구 유출액. 필요 금액이므로 **올림**한다 (모자란 이체를 만들지 않기 위해)."""
    return quantize_up(sum((o.amount_usdc for o in obligations), ZERO))


def _treasury_available(context: DecisionContext) -> Decimal:
    """트레저리에서 실제로 꺼낼 수 있는 금액.

    vault 의 min_buffer 는 침해하지 않는다. 나가는 금액이므로 **내림**한다.
    """
    vault = context.wallet(TREASURY_WALLET_ID)
    if vault is None:
        return ZERO
    return quantize_down(max(ZERO, vault.usdc_balance - vault.min_buffer_usdc))


def build_forecast(context: DecisionContext, now: datetime) -> Forecast:
    """지갑별 부족분과 이체 후보를 계산한다.

    ``shortfall = max(0, required_outflow + min_buffer - usdc_balance)``
    """
    policy = context.policy
    in_horizon = obligations_in_horizon(
        context.obligations, now, policy.forecast_horizon_hours
    )

    by_wallet: dict[str, list[Obligation]] = {}
    for obligation in in_horizon:
        by_wallet.setdefault(obligation.wallet_id, []).append(obligation)

    treasury_available = _treasury_available(context)

    shortfalls: list[WalletShortfall] = []
    options: list[FundingOption] = []
    total_shortfall = ZERO

    for wallet in sorted(context.wallets, key=lambda w: w.wallet_id):
        wallet_obligations = by_wallet.get(wallet.wallet_id, [])
        required = _required_outflow(wallet_obligations)
        shortfall = quantize_up(
            max(ZERO, required + wallet.min_buffer_usdc - wallet.usdc_balance)
        )
        earliest = (
            min(o.due_at for o in wallet_obligations) if wallet_obligations else None
        )

        shortfalls.append(
            WalletShortfall(
                wallet_id=wallet.wallet_id,
                current_balance=wallet.usdc_balance,
                required_outflow=required,
                min_buffer=wallet.min_buffer_usdc,
                shortfall=shortfall,
                earliest_due_at=earliest,
            )
        )

        if shortfall <= ZERO:
            continue
        total_shortfall += shortfall

        # 트레저리 자신은 이체 대상이 되지 않는다. vault 가 부족하면 그건
        # is_treasury_insufficient 로 드러나야 할 문제이지 vault -> vault
        # 이체로 풀 수 있는 문제가 아니다.
        if wallet.wallet_id == TREASURY_WALLET_ID:
            continue

        options.extend(
            _funding_options(
                wallet_id=wallet.wallet_id,
                shortfall=shortfall,
                min_buffer=wallet.min_buffer_usdc,
                policy=policy,
                treasury_available=treasury_available,
            )
        )

    total_shortfall = quantize_up(total_shortfall)

    return Forecast(
        shortfalls=shortfalls,
        funding_options=options,
        treasury_available_usdc=treasury_available,
        total_shortfall_usdc=total_shortfall,
        is_treasury_insufficient=total_shortfall > treasury_available,
    )


def _funding_options(
    *,
    wallet_id: str,
    shortfall: Decimal,
    min_buffer: Decimal,
    policy: Policy,
    treasury_available: Decimal,
) -> list[FundingOption]:
    """부족한 지갑 하나에 대한 이체 후보 3종.

    금액은 전부 **내림**한다 — 나가는 돈이기 때문이다. ``_exact`` 만 예외로
    부족분(이미 올림된 값)을 그대로 쓴다. 여기서 내리면 1 base unit 모자란
    이체가 되어 존재 이유가 사라진다.

    금액이 0 이하인 후보는 만들지 않는다. 집행 불가능한 선택지를 LLM 에게
    보여주면 그걸 고를 뿐이고, 가드가 폐기하면서 아무것도 안 한 셈이 된다.
    """
    buffered = quantize_down(shortfall + min_buffer)
    capped = quantize_down(
        min(buffered, policy.max_per_transfer_usdc, treasury_available)
    )

    candidates = [
        (f"{wallet_id}_exact", shortfall, "부족분 정확히"),
        (f"{wallet_id}_buffered", buffered, "부족분 + 최소 버퍼"),
        (f"{wallet_id}_max", capped, "한도·트레저리 여력 안에서 최대"),
    ]
    return [
        FundingOption(
            option_id=option_id,
            target_wallet_id=wallet_id,
            amount_usdc=amount,
            label=label,
        )
        for option_id, amount, label in candidates
        if amount > ZERO
    ]


# --------------------------------------------------------------------------- #
# 폴백 계획
# --------------------------------------------------------------------------- #


def _wallet_rank(
    wallet_id: str, obligations: list[Obligation], now: datetime, horizon_hours: int
) -> tuple[int, datetime]:
    """정렬 키: (카테고리 순위, 최근접 만기).

    한 지갑이 여러 카테고리의 지출을 가질 수 있으므로 **가장 급한 카테고리**를
    그 지갑의 순위로 삼는다. 급여가 하나라도 걸린 지갑은 급여 지갑이다.
    """
    relevant = [
        o
        for o in obligations_in_horizon(obligations, now, horizon_hours)
        if o.wallet_id == wallet_id
    ]
    if not relevant:
        return _LOWEST_RANK, datetime.max.replace(tzinfo=now.tzinfo)
    rank = min(CATEGORY_RANK.get(o.category, _LOWEST_RANK) for o in relevant)
    earliest = min(o.due_at for o in relevant)
    return rank, earliest


def _severity(due_at: datetime | None, now: datetime, insufficient: bool) -> RiskLevel:
    """만기까지 남은 시간으로 심각도를 매긴다."""
    if insufficient:
        return "CRITICAL"
    if due_at is None:
        return "LOW"
    remaining = due_at - now
    if remaining <= timedelta(0):
        return "CRITICAL"
    if remaining < IMMINENT_WINDOW:
        return "HIGH"
    if remaining < timedelta(hours=72):
        return "MEDIUM"
    return "LOW"


def build_fallback_plan(
    forecast: Forecast, context: DecisionContext, now: datetime
) -> DecisionPlan:
    """LLM 없이 만드는 계획. 결정론적이며 항상 유효하다.

    선택 규칙
    ---------
    - 트레저리가 부족하면 ``ESCALATE`` 단독. 부분 집행은 하지 않는다.
      돈이 모자란 상태에서 먼저 온 순서대로 나눠주면 나중에 오는 급여가
      말없이 실패한다. 사람이 우선순위를 정해야 하는 상황이다.
    - 부족분이 없으면 ``NOOP`` 단독, 위험도 LOW.
    - 그 외에는 지갑마다 ``TRANSFER`` 하나씩, ``{wid}_exact`` 를 고른다.
      ``_max`` 를 고르면 앞선 지갑이 트레저리를 다 먹어 뒤 지갑이 굶는다.
      최소 충분 금액이 커버 가능한 지갑 수를 최대화한다.

    ``confidence`` 는 1.0 이다. 이 계획에는 추정이 없다.
    """
    horizon = context.policy.forecast_horizon_hours
    positive = [s for s in forecast.shortfalls if s.shortfall > ZERO]
    short_by_wallet = {s.wallet_id: s.shortfall for s in positive}

    at_risk = [
        AtRiskObligation(
            obligation_id=o.obligation_id,
            severity=_severity(o.due_at, now, forecast.is_treasury_insufficient),
            reason_ko=(
                f"{o.wallet_id} 지갑의 예측 잔고가 부족하다 "
                f"(부족분 {short_by_wallet[o.wallet_id]} USDC)."
            ),
        )
        for o in obligations_in_horizon(context.obligations, now, horizon)
        if o.wallet_id in short_by_wallet
    ]

    if forecast.is_treasury_insufficient:
        return DecisionPlan(
            overall_risk="CRITICAL",
            at_risk=at_risk,
            actions=[
                ProposedAction(
                    action_id="fallback_escalate",
                    type="ESCALATE",
                    timing="NOW",
                    priority=1,
                    rationale_ko=(
                        f"총 부족분 {forecast.total_shortfall_usdc} USDC 가 "
                        f"트레저리 가용액 {forecast.treasury_available_usdc} USDC 를 "
                        "초과한다. 자동 집행으로는 해결할 수 없어 사람 판단이 필요하다."
                    ),
                )
            ],
            reasoning_summary_ko="트레저리 가용액 부족 — 결정론적 폴백이 에스컬레이션했다.",
            confidence=1.0,
            escalation_required=True,
            escalation_reason_ko=(
                f"부족분 {forecast.total_shortfall_usdc} USDC > "
                f"가용액 {forecast.treasury_available_usdc} USDC"
            ),
        )

    if not positive:
        return DecisionPlan(
            overall_risk="LOW",
            at_risk=[],
            actions=[
                ProposedAction(
                    action_id="fallback_noop",
                    type="NOOP",
                    timing="NOW",
                    priority=1,
                    rationale_ko="예측 구간 안에서 부족한 지갑이 없다. 이체하지 않는다.",
                )
            ],
            reasoning_summary_ko="모든 지갑이 예정 지출과 최소 버퍼를 감당한다.",
            confidence=1.0,
            escalation_required=False,
        )

    fundable = [
        shortfall
        for shortfall in positive
        if shortfall.wallet_id != TREASURY_WALLET_ID
        and forecast.option(f"{shortfall.wallet_id}_exact") is not None
    ]
    ordered = sorted(
        fundable,
        key=lambda s: _wallet_rank(s.wallet_id, context.obligations, now, horizon),
    )

    actions: list[ProposedAction] = []
    highest: RiskLevel = "LOW"
    for index, shortfall in enumerate(ordered, start=1):
        due_at = shortfall.earliest_due_at
        imminent = due_at is not None and (due_at - now) < IMMINENT_WINDOW
        severity = _severity(due_at, now, insufficient=False)
        if _RISK_ORDER[severity] > _RISK_ORDER[highest]:
            highest = severity

        actions.append(
            ProposedAction(
                action_id=f"fallback_fund_{shortfall.wallet_id}",
                type="TRANSFER",
                funding_option_id=f"{shortfall.wallet_id}_exact",
                from_wallet_id=TREASURY_WALLET_ID,
                to_wallet_id=shortfall.wallet_id,
                source_token="USDC",
                max_slippage_bps=None,
                timing="NOW" if imminent else "SCHEDULED",
                # 만기 24시간 전에 집행한다. 확정 지연·재시도에 쓸 여유를 남긴다.
                scheduled_for=None if imminent else (due_at - IMMINENT_WINDOW if due_at else None),
                priority=index,
                rationale_ko=(
                    f"{shortfall.wallet_id} 부족분 {shortfall.shortfall} USDC 를 "
                    "트레저리에서 최소 충분 금액만 보충한다."
                ),
            )
        )

    if not actions:
        # 부족분은 있는데 이체 가능한 후보가 없다 — vault 자신이 부족하거나
        # 모든 후보 금액이 0 이 된 경우다. 조용히 넘기면 안 된다.
        return DecisionPlan(
            overall_risk="CRITICAL",
            at_risk=at_risk,
            actions=[
                ProposedAction(
                    action_id="fallback_escalate_no_option",
                    type="ESCALATE",
                    timing="NOW",
                    priority=1,
                    rationale_ko="부족분이 있으나 집행 가능한 이체 후보가 없다.",
                )
            ],
            reasoning_summary_ko="집행 가능한 이체 후보가 없어 에스컬레이션한다.",
            confidence=1.0,
            escalation_required=True,
            escalation_reason_ko="funding_option 이 하나도 생성되지 않았다.",
        )

    return DecisionPlan(
        overall_risk=highest,
        at_risk=at_risk,
        actions=actions,
        reasoning_summary_ko=(
            f"{len(actions)}개 지갑을 급여 > 벤더 > API 순으로 보충한다 (결정론적 폴백)."
        ),
        confidence=1.0,
        escalation_required=False,
    )


_RISK_ORDER: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


__all__ = [
    "CATEGORY_RANK",
    "IMMINENT_WINDOW",
    "TREASURY_WALLET_ID",
    "build_fallback_plan",
    "build_forecast",
    "obligations_in_horizon",
]
