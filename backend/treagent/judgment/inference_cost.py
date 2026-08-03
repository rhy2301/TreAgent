"""추론비를 Operations Wallet 의 실제 예정 지출로 기표한다.

재귀 루프
---------
에이전트가 x402 로 자기 추론 비용을 결제한다 -> 그 지출이 다음 사이클의
:class:`~treagent.judgment.models.Obligation` 이 된다 -> Forecaster 의
``required_outflow`` 에 잡힌다 -> Operations Wallet 부족분이 생긴다 ->
Vault -> Operations 충전이 **Devnet 에서 진짜로 집행된다.**

기표하지 않으면 이 루프가 끊긴다. 추론비를 스냅샷에만 표시하고 obligation 으로
넣지 않으면 예측기는 그 지출을 모르고, 충전 액션도 나오지 않는다.

실측 청구액과 가정 단가를 섞지 않는다
-------------------------------------
``ProviderResult.cost_usdc`` 는 **이번 한 번의 실측 청구액**이다 (게이트웨이가
402 챌린지에 고지하고 온체인 ``transferChecked`` 로 실제로 나간 금액).

반면 obligation 의 금액은 **앞으로 하루치 예상 지출**이라 성격이 다르다. 한 번
쓴 0.05 USDC 를 하루 예산으로 삼으면 부족분이 사실상 0 이 되고, 반대로 실측치를
그대로 96배 하면 이번 호출이 유난히 비쌌을 때 예측이 통째로 흔들린다. 그래서
가정 단가(``assumed_unit_cost``)와 게이팅 기준 호출 횟수(``projected_daily_calls``)
로 계산하고, **두 값을 스냅샷에 함께 실어 화면에서 밝힌다.**

두 숫자를 같은 필드에 담지 마라. 실측치는 ``cost_usdc``, 가정치는
``assumed_unit_cost_usdc`` / ``projected_daily_calls`` 로 끝까지 분리한다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal

from treagent.judgment.models import (
    DecisionContext,
    Obligation,
    coerce_usdc,
    quantize_up,
)

OPERATIONS_WALLET_ID = "operations"
"""추론비를 내는 지갑. 이 에이전트가 관리하는 지갑 중 하나라는 점이 데모의 핵심이다."""

DEFAULT_ASSUMED_UNIT_COST = "0.05"
"""호출 1회당 가정 단가 (USDC). 자체 게이트웨이의 고지 단가와 같게 맞춰뒀다."""

PROJECTED_DAILY_CALLS = 96
"""하루 예상 호출 수.

게이팅에서 나온 숫자다. :data:`~treagent.judgment.gating.STALE_DECISION_AGE` 가
6시간이므로 아무 일도 없어도 하루 4회는 돈다. 여기에 잔고 변동·신규 지출·만기
임박 트리거를 얹으면 15분 간격이 상한이 되고, 24시간 / 15분 = 96 이다.
**상한을 쓰는 것이 의도다** — 추론 예산은 모자라는 쪽이 위험하다.
"""


def assumed_unit_cost_usdc() -> Decimal:
    """가정 단가. ``TREAGENT_ASSUMED_INFERENCE_COST_USDC`` 로 덮어쓴다."""
    raw = os.getenv("TREAGENT_ASSUMED_INFERENCE_COST_USDC", DEFAULT_ASSUMED_UNIT_COST)
    return coerce_usdc(raw)


def projected_daily_cost_usdc(
    unit_cost: Decimal | None = None, daily_calls: int = PROJECTED_DAILY_CALLS
) -> Decimal:
    """하루치 예상 추론비. 필요 금액이므로 **올림**한다."""
    return quantize_up((unit_cost or assumed_unit_cost_usdc()) * daily_calls)


def inference_obligation_id(now: datetime) -> str:
    """``ob_inference_20260803``.

    날짜 단위 id 라서 같은 날 여러 번 판단해도 obligation 은 하나로 유지된다
    (upsert). 사이클마다 새 id 를 만들면 하루 96건이 쌓여 부족분이 96배로
    부풀고, ``context_hash`` 도 매번 바뀌어 게이팅 캐시가 무력화된다.
    """
    return f"ob_inference_{now.strftime('%Y%m%d')}"


def build_inference_obligation(
    now: datetime,
    *,
    unit_cost: Decimal | None = None,
    daily_calls: int = PROJECTED_DAILY_CALLS,
) -> Obligation:
    """오늘치 추론비 obligation 하나."""
    unit = unit_cost or assumed_unit_cost_usdc()
    amount = projected_daily_cost_usdc(unit, daily_calls)
    # 만기는 그 날의 끝이다. 추론비는 하루 동안 조금씩 나가므로 특정 시각에
    # 몰리지 않지만, 예측기는 만기 하나를 요구한다. 날의 끝으로 잡으면 그날
    # 안에는 확실히 커버되고, 24시간 임박 창에도 자연스럽게 들어온다.
    due_at = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return Obligation(
        obligation_id=inference_obligation_id(now),
        wallet_id=OPERATIONS_WALLET_ID,
        amount_usdc=amount,
        due_at=due_at,
        category="API_USAGE",
        recipient_count=1,
        memo=(
            f"x402 추론비 예상 지출 (가정 단가 {unit} USDC x 일 {daily_calls}회). "
            "실측 청구액은 스냅샷의 inference.cost_usdc 를 보라."
        ),
        is_recurring=True,
    )


def book_inference_cost(
    context: DecisionContext,
    now: datetime,
    *,
    cost_usdc: Decimal | None,
    unit_cost: Decimal | None = None,
    daily_calls: int = PROJECTED_DAILY_CALLS,
) -> tuple[DecisionContext, str | None]:
    """다음 사이클 컨텍스트에 추론비 obligation 을 upsert 한다.

    ``cost_usdc`` 가 ``None`` 이면 아무것도 하지 않는다 — 결제가 일어나지
    않았다는 뜻이고 (mock/aistudio/폴백 경로), 없는 지출을 기표하면 안 된다.
    **다만 그 값을 금액으로 쓰지는 않는다.** 결제가 있었다는 사실만 트리거로
    삼고, 금액은 가정 단가로 계산한다 (모듈 docstring 참조).

    ``DecisionContext`` 는 frozen 이므로 새 인스턴스를 돌려준다. 원본을 그대로
    두는 것이 중요하다 — 이번 사이클의 ``context_hash`` 가 바뀌면 방금 발행한
    ``intent_id`` 의 멱등성이 깨진다.

    Returns:
        ``(새 컨텍스트, 기표된 obligation_id)``. 기표하지 않았으면 id 는 ``None``.
    """
    if cost_usdc is None:
        return context, None

    obligation = build_inference_obligation(
        now, unit_cost=unit_cost, daily_calls=daily_calls
    )
    remaining = [
        existing
        for existing in context.obligations
        if existing.obligation_id != obligation.obligation_id
    ]
    return (
        context.model_copy(update={"obligations": [*remaining, obligation]}),
        obligation.obligation_id,
    )


def estimated_runway_days(
    balance_usdc: Decimal | None,
    unit_cost: Decimal | None = None,
    daily_calls: int = PROJECTED_DAILY_CALLS,
) -> Decimal | None:
    """잔고로 며칠 더 추론할 수 있는가.

    **일수이지 호출 수가 아니다.** ``balance / (단가 x 일일 호출수)`` 다.
    이 값이 ``policy.inference_budget_min_days`` 아래로 내려가면 호출을 멈추고
    Vault -> Operations 충전을 최우선으로 올린다.
    """
    if balance_usdc is None:
        return None
    daily = projected_daily_cost_usdc(unit_cost, daily_calls)
    if daily <= 0:
        return None
    return (balance_usdc / daily).quantize(Decimal("0.01"))


__all__ = [
    "OPERATIONS_WALLET_ID",
    "PROJECTED_DAILY_CALLS",
    "assumed_unit_cost_usdc",
    "book_inference_cost",
    "build_inference_obligation",
    "estimated_runway_days",
    "inference_obligation_id",
    "projected_daily_cost_usdc",
]
