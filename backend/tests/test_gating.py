"""gating 검증 — 6개 트리거 각각 on/off, 스킵 경로, 예산 거부권."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import Scenario, load_scenario
from treagent.judgment import forecaster, gating
from treagent.judgment.gating import GatingState, obligation_fingerprint
from treagent.judgment.models import (
    DecisionContext,
    Forecast,
    Obligation,
    Policy,
    WalletState,
)


@pytest.fixture
def base() -> tuple[Scenario, Forecast]:
    scenario = load_scenario("normal_shortfall")
    return scenario, forecaster.build_forecast(scenario.context, scenario.now)


def primed(scenario: Scenario, forecast: Forecast) -> GatingState:
    """직전 사이클을 막 끝낸 상태."""
    state = GatingState()
    plan = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)
    state.remember(scenario.context, forecast, plan, scenario.now)
    return state


def with_balance(
    context: DecisionContext, wallet_id: str, balance: str
) -> DecisionContext:
    wallets = [
        w.model_copy(update={"usdc_balance": Decimal(balance)})
        if w.wallet_id == wallet_id
        else w
        for w in context.wallets
    ]
    return context.model_copy(update={"wallets": wallets})


def evaluate(
    context: DecisionContext, state: GatingState, now: datetime
) -> gating.GatingDecision:
    forecast = forecaster.build_forecast(context, now)
    return gating.evaluate(context, forecast, state, now)


# --------------------------------------------------------------------------- #
# 1. 잔고 변동 >= max(1%, 100 USDC)
# --------------------------------------------------------------------------- #


class TestBalanceMoved:
    def test_off_when_below_both_thresholds(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        # payroll 500 -> 550. 1% = 5, 하한 100. 변동 50 은 둘 다 못 넘는다.
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now)
        assert "BALANCE_MOVED" not in result.triggers

    def test_on_when_over_the_floor(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        context = with_balance(scenario.context, "payroll", "700")
        result = evaluate(context, state, scenario.now)
        assert "BALANCE_MOVED" in result.triggers
        assert result.should_call is True

    def test_on_when_over_one_percent_of_a_large_balance(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        # vault 50000 의 1% = 500 > 하한 100. 600 움직이면 켜진다.
        context = with_balance(scenario.context, "vault", "50600")
        result = evaluate(context, state, scenario.now)
        assert "BALANCE_MOVED" in result.triggers

    def test_off_when_below_one_percent_of_a_large_balance(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        context = with_balance(scenario.context, "vault", "50400")
        result = evaluate(context, state, scenario.now)
        assert "BALANCE_MOVED" not in result.triggers


# --------------------------------------------------------------------------- #
# 2. 신규/변경 obligation
# --------------------------------------------------------------------------- #


class TestObligationChanged:
    def test_off_when_identical(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now)
        assert "OBLIGATION_CHANGED" not in result.triggers

    def test_on_when_amount_changes(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        obligations = [
            o.model_copy(update={"amount_usdc": Decimal(12001)})
            if o.obligation_id == "ob_payroll_aug"
            else o
            for o in scenario.context.obligations
        ]
        context = scenario.context.model_copy(update={"obligations": obligations})
        result = evaluate(context, state, scenario.now)
        assert "OBLIGATION_CHANGED" in result.triggers

    def test_on_when_a_new_obligation_appears(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        extra = Obligation(
            obligation_id="ob_new",
            wallet_id="vendor",
            amount_usdc=Decimal(100),
            due_at=scenario.now + timedelta(days=5),
            category="VENDOR",
            recipient_count=1,
            memo="신규",
            is_recurring=False,
        )
        context = scenario.context.model_copy(
            update={"obligations": [*scenario.context.obligations, extra]}
        )
        result = evaluate(context, state, scenario.now)
        assert "OBLIGATION_CHANGED" in result.triggers

    def test_memo_edits_do_not_trigger(self, base: tuple[Scenario, Forecast]) -> None:
        """메모 수정으로 추론 비용이 나가면 안 된다. 인젝션 문구 변조도 마찬가지다."""
        scenario, _ = base
        original = scenario.context.obligations[0]
        edited = original.model_copy(update={"memo": "완전히 다른 메모"})
        assert obligation_fingerprint(original) == obligation_fingerprint(edited)


# --------------------------------------------------------------------------- #
# 3. shortfall 0 -> 양수 전이
# --------------------------------------------------------------------------- #


class TestShortfallAppeared:
    def test_off_when_already_short(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now)
        assert "SHORTFALL_APPEARED" not in result.triggers

    def test_on_when_a_healthy_wallet_goes_short(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        # operations 는 직전에 건전했다. 잔고가 마르면 새 부족분이 생긴다.
        context = with_balance(scenario.context, "operations", "0")
        result = evaluate(context, state, scenario.now)
        assert "SHORTFALL_APPEARED" in result.triggers


# --------------------------------------------------------------------------- #
# 4. 24h 임박 구간 진입
# --------------------------------------------------------------------------- #


class TestObligationImminent:
    def test_off_when_already_imminent(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now)
        assert "OBLIGATION_IMMINENT" not in result.triggers

    def test_on_when_an_obligation_enters_the_window(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        # vendor 건은 3일 뒤였다. 시계를 밀어 24h 안으로 들어오게 한다.
        later = scenario.now + timedelta(hours=50)
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, later)
        assert "OBLIGATION_IMMINENT" in result.triggers


# --------------------------------------------------------------------------- #
# 5. 마지막 판단 이후 6h
# --------------------------------------------------------------------------- #


class TestDecisionStale:
    def test_off_before_six_hours(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now + timedelta(hours=5))
        assert "DECISION_STALE" not in result.triggers

    def test_on_after_six_hours(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now + timedelta(hours=7))
        assert "DECISION_STALE" in result.triggers
        assert result.should_call is True

    def test_stale_alone_does_not_override_an_unchanged_context(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """아무것도 안 바뀐 상태를 6시간마다 다시 묻는 것은 순수한 비용이다."""
        scenario, forecast = base
        state = primed(scenario, forecast)
        result = evaluate(scenario.context, state, scenario.now + timedelta(hours=7))
        assert result.should_call is False
        assert result.skip_reason == "CONTEXT_UNCHANGED"
        assert "DECISION_STALE" in result.triggers


# --------------------------------------------------------------------------- #
# 6. 예약 액션 실행 시각 도래
# --------------------------------------------------------------------------- #


class TestScheduledActionDue:
    def test_off_when_still_in_the_future(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        state.pending_scheduled = [scenario.now + timedelta(hours=3)]
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now)
        assert "SCHEDULED_ACTION_DUE" not in result.triggers

    def test_on_when_the_time_has_come(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        state.pending_scheduled = [scenario.now - timedelta(minutes=1)]
        context = with_balance(scenario.context, "payroll", "550")
        result = evaluate(context, state, scenario.now)
        assert "SCHEDULED_ACTION_DUE" in result.triggers

    def test_due_schedule_overrides_an_unchanged_context(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """집행 시각이 실제로 도래했다면 상태가 그대로여도 행동이 필요하다."""
        scenario, forecast = base
        state = primed(scenario, forecast)
        state.pending_scheduled = [scenario.now - timedelta(minutes=1)]
        result = evaluate(scenario.context, state, scenario.now)
        assert result.should_call is True
        assert result.skip_reason is None


# --------------------------------------------------------------------------- #
# 스킵 경로
# --------------------------------------------------------------------------- #


class TestSkip:
    def test_identical_context_returns_the_cached_plan(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        state = primed(scenario, forecast)
        result = evaluate(scenario.context, state, scenario.now)

        assert result.should_call is False
        assert result.skipped is True
        assert result.skip_reason == "CONTEXT_UNCHANGED"
        assert result.cached_plan is state.last_plan

    def test_first_cycle_always_calls(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        result = gating.evaluate(
            scenario.context, forecast, GatingState(), scenario.now
        )
        assert result.should_call is True
        assert "OBLIGATION_CHANGED" in result.triggers

    def test_no_trigger_skips(self) -> None:
        """비교 기준은 있는데 아무 트리거도 없고 해시만 다른 경우."""
        now = datetime.fromisoformat("2026-08-03T09:00:00+00:00")
        policy = Policy.model_validate(
            {
                "max_per_transfer_usdc": "10000",
                "max_daily_total_usdc": "50000",
                "daily_spent_usdc": "0",
                "max_slippage_bps": 50,
                "whitelisted_wallet_ids": ["vault"],
                "forecast_horizon_hours": 168,
                "inference_budget_min_days": "30",
            }
        )
        wallet = WalletState(
            wallet_id="vault",
            address="addr_vault",
            usdc_balance=Decimal(50000),
            sol_balance=Decimal(1),
            min_buffer_usdc=Decimal(10000),
        )
        context = DecisionContext(
            generated_at=now, wallets=[wallet], obligations=[], policy=policy
        )
        forecast = forecaster.build_forecast(context, now)

        state = GatingState()
        plan = forecaster.build_fallback_plan(forecast, context, now)
        state.remember(context, forecast, plan, now)
        # 정책만 살짝 바꿔 해시를 흔든다 (트리거 조건에는 아무것도 안 걸린다).
        moved = context.model_copy(
            update={
                "policy": policy.model_copy(
                    update={"max_slippage_bps": 51}
                )
            }
        )
        result = gating.evaluate(
            moved, forecaster.build_forecast(moved, now), state, now
        )
        assert result.should_call is False
        assert result.skip_reason == "NO_TRIGGER"


# --------------------------------------------------------------------------- #
# 추론 예산 거부권
# --------------------------------------------------------------------------- #


class TestInferenceBudget:
    def test_low_budget_vetoes_every_trigger(self) -> None:
        scenario = load_scenario("inference_budget_low")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        state = GatingState(inference_budget_days=scenario.inference_budget_days)

        result = gating.evaluate(scenario.context, forecast, state, scenario.now)
        assert result.should_call is False
        assert result.skip_reason == "INFERENCE_BUDGET_LOW"
        # 트리거 자체는 켜져 있었다 — 예산이 그것을 눌렀을 뿐이다.
        assert result.triggers

    def test_sufficient_budget_does_not_veto(self) -> None:
        scenario = load_scenario("inference_budget_low")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        state = GatingState(inference_budget_days=Decimal(90))

        result = gating.evaluate(scenario.context, forecast, state, scenario.now)
        assert result.should_call is True

    def test_unknown_budget_does_not_veto(self) -> None:
        """예산을 모를 때 부르지 않으면 B 세션 이전에는 LLM 이 영원히 안 붙는다."""
        scenario = load_scenario("inference_budget_low")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        result = gating.evaluate(
            scenario.context, forecast, GatingState(), scenario.now
        )
        assert result.should_call is True
