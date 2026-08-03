"""PolicyGuard 검증 — 10개 검사 각각의 통과/실패 한 쌍.

네트워크는 한 번도 타지 않는다. 8번 검사의 잔고 재조회는
:class:`~tests.conftest.StubBalanceProvider` 가 받는다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar

import pytest

from tests.conftest import (
    Scenario,
    StubBalanceProvider,
    balances_from_context,
    load_scenario,
)
from treagent.judgment import forecaster
from treagent.judgment.guard import (
    CollectingAlertHook,
    GuardResult,
    InMemoryIntentLedger,
    PolicyGuard,
)
from treagent.judgment.models import (
    DecisionContext,
    DecisionPlan,
    Forecast,
    Policy,
    ProposedAction,
    make_intent_id,
)

T = TypeVar("T")

DECISION_ID = "dec_test"


def run(coro: Coroutine[Any, Any, T]) -> T:
    """이벤트 루프 없이 코루틴 하나를 돌린다 (pytest 플러그인 불필요)."""
    return asyncio.run(coro)


def action(
    action_id: str,
    *,
    type: str = "TRANSFER",
    option: str | None = None,
    to: str | None = None,
    frm: str | None = "vault",
    priority: int = 1,
    slippage: int | None = None,
    timing: str = "NOW",
    scheduled_for: datetime | None = None,
    source_token: str | None = "USDC",
) -> ProposedAction:
    return ProposedAction.model_validate(
        {
            "action_id": action_id,
            "type": type,
            "funding_option_id": option,
            "from_wallet_id": frm,
            "to_wallet_id": to,
            "source_token": source_token,
            "max_slippage_bps": slippage,
            "timing": timing,
            "scheduled_for": scheduled_for,
            "priority": priority,
            "rationale_ko": "테스트",
        }
    )


def plan(*actions: ProposedAction, confidence: float = 0.9) -> DecisionPlan:
    return DecisionPlan(
        overall_risk="HIGH",
        at_risk=[],
        actions=list(actions),
        reasoning_summary_ko="테스트 계획",
        confidence=confidence,
        escalation_required=False,
    )


def make_guard(
    scenario: Scenario,
    *,
    balances: dict[str, Decimal] | None = None,
    ledger: InMemoryIntentLedger | None = None,
) -> tuple[PolicyGuard, StubBalanceProvider, CollectingAlertHook]:
    provider = StubBalanceProvider(
        balances=balances
        if balances is not None
        else balances_from_context(scenario.context)
    )
    hook = CollectingAlertHook()
    return (
        PolicyGuard(provider, ledger=ledger or InMemoryIntentLedger(), alerts=hook),
        provider,
        hook,
    )


def enforce(
    guard: PolicyGuard,
    decision: DecisionPlan,
    context: DecisionContext,
    forecast: Forecast,
    now: datetime,
) -> GuardResult:
    return run(guard.enforce(decision, context, forecast, now, DECISION_ID))


@pytest.fixture
def base() -> tuple[Scenario, Forecast]:
    scenario = load_scenario("normal_shortfall")
    return scenario, forecaster.build_forecast(scenario.context, scenario.now)


# --------------------------------------------------------------------------- #
# 1. 스키마 파싱
# --------------------------------------------------------------------------- #


class TestCheck1Schema:
    def test_pass_valid_json_parses(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, _ = base
        guard, _, _ = make_guard(scenario)
        parsed = guard.parse_plan(scenario.llm_response_text)
        assert parsed is not None
        assert parsed.confidence == 0.87

    def test_fail_malformed_json_returns_none(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, _ = base
        guard, _, hook = make_guard(scenario)
        assert guard.parse_plan("{not json at all") is None
        assert [a.kind for a in hook.alerts] == ["SCHEMA_INVALID"]

    def test_fail_wrong_enum_returns_none(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, _ = base
        guard, _, hook = make_guard(scenario)
        payload = dict(scenario.llm_response)
        payload["overall_risk"] = "APOCALYPTIC"
        assert guard.parse_plan(json.dumps(payload)) is None
        assert hook.alerts

    def test_injected_amount_field_is_rejected_at_parse(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """계약에 없는 금액 필드를 끼워 넣으면 조용히 무시되지 않고 파싱이 실패한다."""
        scenario, _ = base
        guard, _, _ = make_guard(scenario)
        payload = json.loads(scenario.llm_response_text)
        payload["actions"][0]["amount_usdc"] = "999999"
        assert guard.parse_plan(json.dumps(payload)) is None


# --------------------------------------------------------------------------- #
# 2. 화이트리스트 — 실패 시 전체 거부
# --------------------------------------------------------------------------- #


class TestCheck2Whitelist:
    def test_pass_all_registered(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "APPROVED"
        assert len(result.intents) == 1

    def test_fail_unregistered_wallet_rejects_whole_plan(self) -> None:
        """미등록 지갑이 섞이면 **정상 액션까지 포함해 전부** 버린다."""
        scenario = load_scenario("prompt_injection")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        guard, _, hook = make_guard(scenario)
        parsed = guard.parse_plan(scenario.llm_response_text)
        assert parsed is not None

        result = enforce(guard, parsed, scenario.context, forecast, scenario.now)

        assert result.verdict == "REJECTED"
        assert result.intents == []
        # 멀쩡해 보이던 act_fund_payroll 도 함께 폐기됐다.
        assert set(result.dropped_action_ids) == {"act_exfiltrate", "act_fund_payroll"}
        assert [a.kind for a in hook.alerts] == ["PROMPT_INJECTION"]
        assert "attacker_wallet" in hook.alerts[0].message

    def test_fail_unregistered_wallet_in_noop_also_alerts(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """자금이 안 나가는 자리에 심어진 주소도 신호다."""
        scenario, forecast = base
        guard, _, hook = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", type="NOOP", to="ghost_wallet", frm=None)),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "REJECTED"
        assert hook.alerts[0].kind == "PROMPT_INJECTION"


# --------------------------------------------------------------------------- #
# 3. funding_option_id 실재
# --------------------------------------------------------------------------- #


class TestCheck3FundingOption:
    def test_pass_existing_option(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert len(result.intents) == 1

    def test_fail_unknown_option_drops_only_that_action(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(
                action("bad", option="vendor_gigantic", to="vendor", priority=1),
                action("good", option="vendor_exact", to="vendor", priority=2),
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.dropped_action_ids == ["bad"]
        assert [i.intent_id for i in result.intents] == [
            make_intent_id(scenario.context.context_hash(), "good")
        ]

    def test_fail_option_target_mismatch_is_dropped(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """payroll 몫으로 계산된 금액을 vendor 로 보내려는 시도."""
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="payroll_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.intents == []
        assert result.dropped_action_ids == ["a1"]


# --------------------------------------------------------------------------- #
# 4. 트레저리 부족 <-> ESCALATE 일관성
# --------------------------------------------------------------------------- #


class TestCheck4TreasuryConsistency:
    def test_pass_escalate_when_insufficient(self) -> None:
        scenario = load_scenario("treasury_insufficient")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        guard, _, _ = make_guard(scenario)
        parsed = guard.parse_plan(scenario.llm_response_text)
        assert parsed is not None

        result = enforce(guard, parsed, scenario.context, forecast, scenario.now)
        assert result.verdict == "APPROVED"
        # ESCALATE 는 자금을 움직이지 않으므로 인텐트가 없다.
        assert result.intents == []

    def test_fail_insufficient_without_escalate_rejects(self) -> None:
        scenario = load_scenario("treasury_insufficient")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        guard, _, hook = make_guard(scenario)

        result = enforce(
            guard,
            plan(action("a1", option="payroll_exact", to="payroll")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "REJECTED"
        assert result.intents == []
        assert hook.alerts[0].kind == "TREASURY_INCONSISTENT"

    def test_fail_escalate_when_treasury_is_fine_rejects(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        guard, _, hook = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", type="ESCALATE", frm=None, option=None)),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "REJECTED"
        assert hook.alerts[0].kind == "TREASURY_INCONSISTENT"

    def test_escalate_with_no_shortfall_is_harmless(self) -> None:
        scenario = load_scenario("all_healthy")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", type="ESCALATE", frm=None, option=None)),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "APPROVED"


# --------------------------------------------------------------------------- #
# 5. 1회 이체 한도 -> CLAMPED (거부 아님)
# --------------------------------------------------------------------------- #


class TestCheck5PerTransferLimit:
    def test_pass_under_limit_is_approved(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.intents[0].guard_verdict == "APPROVED"
        assert result.intents[0].amount_usdc == Decimal(2500)

    def test_fail_over_limit_is_clamped_not_rejected(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """한도 초과는 잘라서 집행한다. 급여를 절반 채우는 게 아예 안 채우는 것보다 낫다."""
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="payroll_exact", to="payroll")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "CLAMPED"
        intent = result.intents[0]
        assert intent.guard_verdict == "CLAMPED"
        # payroll_exact 는 13500, 1회 한도는 10000
        assert intent.amount_usdc == scenario.context.policy.max_per_transfer_usdc
        assert any("[5]" in note for note in intent.guard_notes)


# --------------------------------------------------------------------------- #
# 6. 일일 누적 한도 -> 낮은 우선순위부터 절삭
# --------------------------------------------------------------------------- #


def _with_policy(scenario: Scenario, **overrides: object) -> DecisionContext:
    policy_data = scenario.context.policy.model_dump()
    policy_data.update(overrides)
    return scenario.context.model_copy(
        update={"policy": Policy.model_validate(policy_data)}
    )


class TestCheck6DailyLimit:
    def test_pass_within_daily_headroom(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(
                action("a1", option="payroll_max", to="payroll", priority=1),
                action("a2", option="vendor_exact", to="vendor", priority=2),
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert [i.amount_usdc for i in result.intents] == [
            Decimal(10000),
            Decimal(2500),
        ]

    def test_fail_truncates_from_lowest_priority(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        # 여력 5000 만 남긴다.
        context = _with_policy(scenario, daily_spent_usdc="45000")
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(
                action("a1", option="payroll_max", to="payroll", priority=1),
                action("a2", option="vendor_exact", to="vendor", priority=2),
            ),
            context,
            forecast,
            scenario.now,
        )
        # 우선순위 1 이 남은 여력까지 절삭돼 살아남고, 2 는 폐기된다.
        assert [i.amount_usdc for i in result.intents] == [Decimal(5000)]
        assert result.intents[0].guard_verdict == "CLAMPED"
        assert result.dropped_action_ids == ["a2"]

    def test_fail_no_headroom_drops_everything(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        context = _with_policy(scenario, daily_spent_usdc="50000")
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            context,
            forecast,
            scenario.now,
        )
        assert result.intents == []
        assert result.dropped_action_ids == ["a1"]


# --------------------------------------------------------------------------- #
# 7. 슬리피지 클램프
# --------------------------------------------------------------------------- #


class TestCheck7Slippage:
    def test_pass_within_policy_is_kept(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor", slippage=20)),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.intents[0].max_slippage_bps == 20
        assert result.intents[0].guard_verdict == "APPROVED"

    def test_fail_over_policy_is_clamped(self) -> None:
        scenario = load_scenario("swap_required")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        guard, _, _ = make_guard(scenario)
        parsed = guard.parse_plan(scenario.llm_response_text)
        assert parsed is not None

        result = enforce(guard, parsed, scenario.context, forecast, scenario.now)
        intent = result.intents[0]
        # 요청 300bps -> 정책 50bps
        assert intent.max_slippage_bps == 50
        assert intent.guard_verdict == "CLAMPED"
        assert any("[7]" in note for note in intent.guard_notes)

    def test_none_falls_back_to_policy_value(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor", slippage=None)),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.intents[0].max_slippage_bps == 50


# --------------------------------------------------------------------------- #
# 8. 서명 직전 잔고 재조회
# --------------------------------------------------------------------------- #


class TestCheck8LiveBalance:
    def test_pass_balance_is_actually_queried(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """캐시된 값이 아니라 조회가 실제로 일어나야 한다."""
        scenario, forecast = base
        guard, provider, _ = make_guard(scenario)
        vault = scenario.context.wallet("vault")
        assert vault is not None

        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert provider.calls == [vault.address]
        assert len(result.intents) == 1

    def test_fail_drained_source_drops_only_that_action(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """LLM 응답을 기다리는 사이 vault 가 줄었다 — 넘치는 액션만 버린다."""
        scenario, forecast = base
        vault = scenario.context.wallet("vault")
        assert vault is not None
        # 가용액 20000 - 10000(버퍼) = 10000. a1 이 다 쓰고 a2 는 못 나간다.
        guard, _, _ = make_guard(
            scenario, balances={vault.address: Decimal(20000)}
        )
        result = enforce(
            guard,
            plan(
                action("a1", option="payroll_max", to="payroll", priority=1),
                action("a2", option="vendor_exact", to="vendor", priority=2),
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert [i.amount_usdc for i in result.intents] == [Decimal(10000)]
        assert result.dropped_action_ids == ["a2"]
        assert any("[8]" in note for note in result.notes)

    def test_fail_source_min_buffer_is_protected(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """잔고가 있어도 소스의 최소 버퍼를 깨는 이체는 나가지 않는다."""
        scenario, forecast = base
        vault = scenario.context.wallet("vault")
        assert vault is not None
        guard, _, _ = make_guard(
            scenario, balances={vault.address: Decimal(11000)}
        )
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        # 11000 - 10000 버퍼 = 1000 < 2500
        assert result.intents == []
        assert result.dropped_action_ids == ["a1"]

    def test_reservations_accumulate_across_actions(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """같은 지갑에서 두 번 나가면 두 번째는 첫 번째를 뺀 잔액을 본다."""
        scenario, forecast = base
        vault = scenario.context.wallet("vault")
        assert vault is not None
        guard, provider, _ = make_guard(
            scenario, balances={vault.address: Decimal(23500)}
        )
        result = enforce(
            guard,
            plan(
                action("a1", option="payroll_max", to="payroll", priority=1),
                action("a2", option="vendor_exact", to="vendor", priority=2),
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        # 23500 - 10000 버퍼 = 13500 가용. 10000 + 2500 = 12500 이므로 둘 다 통과.
        assert [i.amount_usdc for i in result.intents] == [
            Decimal(10000),
            Decimal(2500),
        ]
        # 같은 주소는 한 번만 조회한다 (사이클 내 일관성).
        assert provider.calls.count(vault.address) == 1


# --------------------------------------------------------------------------- #
# 9. intent_id 24시간 중복 -> 스킵 (멱등)
# --------------------------------------------------------------------------- #


class TestCheck9Idempotency:
    def test_pass_first_time_emits(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert len(result.intents) == 1

    def test_fail_same_context_twice_transfers_once(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """같은 DecisionContext 를 두 번 처리해도 이체는 한 번이다."""
        scenario, forecast = base
        ledger = InMemoryIntentLedger()
        guard, _, _ = make_guard(scenario, ledger=ledger)
        decision = plan(action("a1", option="vendor_exact", to="vendor"))

        first = enforce(guard, decision, scenario.context, forecast, scenario.now)
        second = enforce(guard, decision, scenario.context, forecast, scenario.now)

        assert len(first.intents) == 1
        assert second.intents == []
        assert any("[9]" in note for note in second.notes)

    def test_intent_id_survives_a_new_guard_instance(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """멱등키는 상태가 아니라 (context_hash, action_id) 에서만 나온다."""
        scenario, forecast = base
        ledger = InMemoryIntentLedger()
        decision = plan(action("a1", option="vendor_exact", to="vendor"))

        guard_a, _, _ = make_guard(scenario, ledger=ledger)
        first = enforce(guard_a, decision, scenario.context, forecast, scenario.now)

        # 프로세스가 재시작된 셈 — 원장만 살아남았다.
        guard_b, _, _ = make_guard(scenario, ledger=ledger)
        second = enforce(guard_b, decision, scenario.context, forecast, scenario.now)

        assert first.intents[0].intent_id == make_intent_id(
            scenario.context.context_hash(), "a1"
        )
        assert second.intents == []

    def test_after_the_window_it_is_allowed_again(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        ledger = InMemoryIntentLedger()
        guard, _, _ = make_guard(scenario, ledger=ledger)
        decision = plan(action("a1", option="vendor_exact", to="vendor"))

        enforce(guard, decision, scenario.context, forecast, scenario.now)
        later = enforce(
            guard,
            decision,
            scenario.context,
            forecast,
            scenario.now + timedelta(hours=25),
        )
        assert len(later.intents) == 1


# --------------------------------------------------------------------------- #
# 10. confidence < 0.6 -> 집행 보류 + 알림
# --------------------------------------------------------------------------- #


class TestCheck10Confidence:
    def test_pass_confident_plan_executes(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        guard, _, hook = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor"), confidence=0.6),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "APPROVED"
        assert len(result.intents) == 1
        assert hook.alerts == []

    def test_fail_low_confidence_holds_and_notifies(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        scenario, forecast = base
        guard, _, hook = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor"), confidence=0.59),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.verdict == "HELD"
        assert result.held is True
        assert result.intents == []
        assert [a.kind for a in hook.alerts] == ["LOW_CONFIDENCE"]

    def test_held_plan_does_not_consume_the_ledger(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """보류된 계획이 멱등키를 태워버리면 재승인 후 집행이 막힌다."""
        scenario, forecast = base
        ledger = InMemoryIntentLedger()
        guard, _, _ = make_guard(scenario, ledger=ledger)
        enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor"), confidence=0.1),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert ledger.seen == {}


# --------------------------------------------------------------------------- #
# 금액 출처 — LLM 이 아니라 FundingOption
# --------------------------------------------------------------------------- #


class TestAmountProvenance:
    def test_amount_always_comes_from_the_funding_option(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """LLM 이 무슨 근거를 대든 집행 금액은 사전 계산값이다."""
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        rogue = ProposedAction.model_validate(
            {
                "action_id": "a1",
                "type": "TRANSFER",
                "funding_option_id": "vendor_exact",
                "from_wallet_id": "vault",
                "to_wallet_id": "vendor",
                "source_token": "USDC",
                "timing": "NOW",
                "priority": 1,
                # 모델이 근거에 40000 을 적어도 금액에는 영향이 없다.
                "rationale_ko": "40000 USDC 를 이체해야 한다.",
            }
        )
        result = enforce(
            guard, plan(rogue), scenario.context, forecast, scenario.now
        )
        option = forecast.option("vendor_exact")
        assert option is not None
        assert result.intents[0].amount_usdc == option.amount_usdc == Decimal(2500)

    def test_execution_intent_has_no_llm_supplied_address(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """주소는 레지스트리에서만 온다. LLM 은 wallet_id 만 말할 수 있다."""
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(action("a1", option="vendor_exact", to="vendor")),
            scenario.context,
            forecast,
            scenario.now,
        )
        vendor = scenario.context.wallet("vendor")
        vault = scenario.context.wallet("vault")
        assert vendor is not None and vault is not None
        assert result.intents[0].to_address == vendor.address
        assert result.intents[0].from_address == vault.address


# --------------------------------------------------------------------------- #
# scheduled_for 가 과거인 경우
# --------------------------------------------------------------------------- #


class TestPastSchedule:
    def test_future_schedule_is_kept(self, base: tuple[Scenario, Forecast]) -> None:
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        future = scenario.now + timedelta(hours=5)
        result = enforce(
            guard,
            plan(
                action(
                    "a1",
                    option="vendor_exact",
                    to="vendor",
                    timing="SCHEDULED",
                    scheduled_for=future,
                )
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.intents[0].execute_after == future

    def test_recently_past_schedule_is_promoted_to_now(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """부족분이 아직 남아 있으므로 늦은 것이지 틀린 것이 아니다."""
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(
                action(
                    "a1",
                    option="vendor_exact",
                    to="vendor",
                    timing="SCHEDULED",
                    scheduled_for=scenario.now - timedelta(hours=3),
                )
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.intents[0].execute_after == scenario.now
        assert any("승격" in note for note in result.intents[0].guard_notes)

    def test_absurdly_past_schedule_is_discarded(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """24시간 넘게 과거인 시각은 지연이 아니라 지어낸 값으로 본다."""
        scenario, forecast = base
        guard, _, _ = make_guard(scenario)
        result = enforce(
            guard,
            plan(
                action(
                    "a1",
                    option="vendor_exact",
                    to="vendor",
                    timing="SCHEDULED",
                    scheduled_for=scenario.now - timedelta(days=400),
                )
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        assert result.intents == []
        assert result.dropped_action_ids == ["a1"]

    def test_discarded_schedule_releases_its_reservation(
        self, base: tuple[Scenario, Forecast]
    ) -> None:
        """폐기된 액션이 잡아둔 자금은 뒤 액션에 돌아가야 한다."""
        scenario, forecast = base
        vault = scenario.context.wallet("vault")
        assert vault is not None
        guard, _, _ = make_guard(
            scenario, balances={vault.address: Decimal(12500)}
        )
        result = enforce(
            guard,
            plan(
                action(
                    "a1",
                    option="vendor_buffered",
                    to="vendor",
                    priority=1,
                    timing="SCHEDULED",
                    scheduled_for=scenario.now - timedelta(days=400),
                ),
                action("a2", option="vendor_exact", to="vendor", priority=2),
            ),
            scenario.context,
            forecast,
            scenario.now,
        )
        # 가용액 2500. a1(3500) 이 살아 있었다면 a2 도 못 나갔을 것이다.
        assert result.dropped_action_ids == ["a1"]
        assert [i.amount_usdc for i in result.intents] == [Decimal(2500)]
