"""추론비 기표 — 재귀 루프의 회귀 테스트.

검증하는 명제는 하나다. **1번째 사이클에서 x402 결제가 일어나면, 2번째
사이클의 Operations Wallet ``required_outflow`` 에 그 비용이 잡혀야 한다.**

이게 깨지면 에이전트는 자기 추론비를 모르는 상태가 되고, Vault -> Operations
충전이 영원히 제안되지 않는다. 데모의 핵심 서사가 통째로 사라지는 회귀다.

네트워크를 타지 않는다 — 공급자는 mock 이고 잔고는 스텁이다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Coroutine, TypeVar

import pytest

from tests.conftest import StubBalanceProvider, balances_from_context, load_scenario
from treagent.judgment import forecaster, inference_cost
from treagent.judgment.engine import JudgmentEngine
from treagent.judgment.guard import PolicyGuard
from treagent.judgment.models import DecisionContext
from treagent.judgment.providers.mock import MockProvider

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def ops_required_outflow(context: DecisionContext, now: datetime) -> Decimal:
    forecast = forecaster.build_forecast(context, now)
    shortfall = forecast.shortfall_for(inference_cost.OPERATIONS_WALLET_ID)
    assert shortfall is not None
    return shortfall.required_outflow


class TestBooking:
    def test_id_is_date_scoped(self) -> None:
        now = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
        assert inference_cost.inference_obligation_id(now) == "ob_inference_20260803"

    def test_amount_uses_assumption_not_measurement(self) -> None:
        """실측 청구액은 **트리거일 뿐** 금액이 아니다."""
        scenario = load_scenario("normal_shortfall")
        # 실측 청구액을 터무니없이 크게 줘도 obligation 금액은 가정치를 따른다.
        booked, obligation_id = inference_cost.book_inference_cost(
            scenario.context, scenario.now, cost_usdc=Decimal("999")
        )
        assert obligation_id == "ob_inference_20260803"
        obligation = next(
            o for o in booked.obligations if o.obligation_id == obligation_id
        )
        expected = inference_cost.assumed_unit_cost_usdc() * (
            inference_cost.PROJECTED_DAILY_CALLS
        )
        assert obligation.amount_usdc == expected
        assert obligation.amount_usdc != Decimal("999")

    def test_no_payment_books_nothing(self) -> None:
        """결제가 없었으면(mock/폴백 경로) 없는 지출을 만들지 않는다."""
        scenario = load_scenario("normal_shortfall")
        booked, obligation_id = inference_cost.book_inference_cost(
            scenario.context, scenario.now, cost_usdc=None
        )
        assert obligation_id is None
        assert booked.obligations == scenario.context.obligations

    def test_upsert_does_not_accumulate(self) -> None:
        """같은 날 96번 판단해도 obligation 은 하나다."""
        scenario = load_scenario("normal_shortfall")
        context = scenario.context
        for _ in range(5):
            context, _ = inference_cost.book_inference_cost(
                context, scenario.now, cost_usdc=Decimal("0.05")
            )
        booked = [
            o for o in context.obligations if o.obligation_id.startswith("ob_inference_")
        ]
        assert len(booked) == 1

    def test_original_context_is_untouched(self) -> None:
        """이번 사이클의 context_hash 가 바뀌면 방금 발행한 intent_id 가 깨진다."""
        scenario = load_scenario("normal_shortfall")
        before = scenario.context.context_hash()
        inference_cost.book_inference_cost(
            scenario.context, scenario.now, cost_usdc=Decimal("0.05")
        )
        assert scenario.context.context_hash() == before


class TestRecursiveLoop:
    def test_second_cycle_sees_inference_cost_in_required_outflow(self) -> None:
        """★ 재귀 루프. 1사이클 결제 -> 2사이클 operations 요구 유출액에 반영."""
        scenario = load_scenario("normal_shortfall")
        balances = StubBalanceProvider(balances_from_context(scenario.context))
        engine = JudgmentEngine(
            guard=PolicyGuard(balances=balances),
            provider=MockProvider(scenario="normal_shortfall"),
        )

        first_outflow = ops_required_outflow(scenario.context, scenario.now)

        # ── 1사이클 ────────────────────────────────────────────────
        run(engine.run_cycle(scenario.context, scenario.now))

        # x402 결제가 있었다고 가정하고 기표한다. mock 공급자는 cost_usdc 를
        # 내지 않으므로 paysh 가 냈을 값을 여기서 넣는다 — 네트워크를 타지
        # 않으면서 기표 경로만 검증하기 위해서다.
        next_context, obligation_id = inference_cost.book_inference_cost(
            scenario.context, scenario.now, cost_usdc=Decimal("0.05")
        )
        assert obligation_id == "ob_inference_20260803"

        # ── 2사이클 ────────────────────────────────────────────────
        later = scenario.now + timedelta(hours=1)
        second_outflow = ops_required_outflow(next_context, later)

        expected_daily = inference_cost.projected_daily_cost_usdc()
        assert second_outflow == first_outflow + expected_daily
        assert second_outflow > first_outflow

        # 2사이클이 실제로 돌아가는지도 본다. 기표가 파이프라인을 깨면 안 된다.
        outcome = run(engine.run_cycle(next_context, later))
        assert outcome.forecast.shortfall_for("operations") is not None

    def test_booked_obligation_is_api_usage_on_operations(self) -> None:
        scenario = load_scenario("normal_shortfall")
        booked, obligation_id = inference_cost.book_inference_cost(
            scenario.context, scenario.now, cost_usdc=Decimal("0.05")
        )
        obligation = next(
            o for o in booked.obligations if o.obligation_id == obligation_id
        )
        assert obligation.category == "API_USAGE"
        assert obligation.wallet_id == "operations"
        assert obligation.is_recurring is True


class TestRunway:
    def test_runway_is_days_not_call_count(self) -> None:
        """``balance / 단가`` 가 아니라 ``balance / (단가 x 일일 호출수)`` 다."""
        unit = Decimal("0.05")
        days = inference_cost.estimated_runway_days(Decimal("480"), unit, 96)
        assert days == Decimal("100.00")

    def test_unknown_balance_yields_none(self) -> None:
        assert inference_cost.estimated_runway_days(None) is None


@pytest.mark.parametrize("daily_calls", [1, 4, 96])
def test_projected_daily_cost_scales(daily_calls: int) -> None:
    unit = Decimal("0.05")
    assert inference_cost.projected_daily_cost_usdc(unit, daily_calls) == unit * (
        daily_calls
    )
