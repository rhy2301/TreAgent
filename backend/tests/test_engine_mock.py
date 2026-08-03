"""MockProvider 로 6개 시나리오 E2E. 네트워크 0회, LLM 호출 0회."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import (
    Scenario,
    StubBalanceProvider,
    balances_from_context,
    load_scenario,
)
from tests.test_guard import run
from treagent.judgment.engine import InMemoryAuditSink, JudgmentEngine
from treagent.judgment.gating import GatingState
from treagent.judgment.guard import CollectingAlertHook, PolicyGuard
from treagent.judgment.providers.mock import MockProvider


def build(
    scenario: Scenario,
    *,
    state: GatingState | None = None,
    balances: dict[str, Decimal] | None = None,
) -> tuple[JudgmentEngine, MockProvider, InMemoryAuditSink, CollectingAlertHook]:
    provider = MockProvider(scenario=scenario.name)
    hook = CollectingAlertHook()
    guard = PolicyGuard(
        StubBalanceProvider(
            balances=balances
            if balances is not None
            else balances_from_context(scenario.context)
        ),
        alerts=hook,
    )
    audit = InMemoryAuditSink()
    engine = JudgmentEngine(
        guard,
        state=state or GatingState(inference_budget_days=Decimal(90)),
        provider=provider,
        audit=audit,
    )
    return engine, provider, audit, hook


# --------------------------------------------------------------------------- #
# 시나리오별 E2E
# --------------------------------------------------------------------------- #


class TestNormalShortfall:
    def test_llm_plan_is_executed_as_proposed(self) -> None:
        scenario = load_scenario("normal_shortfall")
        engine, provider, audit, _ = build(scenario)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "LLM"
        assert len(provider.calls) == 1
        assert outcome.guard.verdict == "APPROVED"

        by_amount = [i.amount_usdc for i in outcome.intents]
        # payroll_max = 10000, vendor_buffered = 3500
        assert by_amount == [Decimal(10000), Decimal(3500)]
        assert outcome.intents[0].execute_after == scenario.now
        assert outcome.intents[1].execute_after == scenario.now + timedelta(days=2)
        assert audit.events[0].skipped is False
        assert audit.events[0].triggers

    def test_provider_never_sees_an_address(self) -> None:
        """원칙 3 — 모델에게 넘어가는 문맥에 주소가 없다."""
        scenario = load_scenario("normal_shortfall")
        engine, provider, _, _ = build(scenario)
        run(engine.run_cycle(scenario.context, scenario.now))

        user = provider.calls[0]["user"]
        for wallet in scenario.context.wallets:
            assert wallet.address not in user

    def test_provider_gets_only_option_ids_for_amounts(self) -> None:
        scenario = load_scenario("normal_shortfall")
        engine, provider, _, _ = build(scenario)
        run(engine.run_cycle(scenario.context, scenario.now))

        schema = provider.calls[0]["schema"]
        action_props = schema["properties"]["actions"]["items"]["properties"]
        # 계획 스키마에는 금액 필드가 아예 없다.
        assert not [key for key in action_props if "amount" in key]
        assert "funding_option_id" in action_props


class TestTreasuryInsufficient:
    def test_escalation_produces_no_intent(self) -> None:
        scenario = load_scenario("treasury_insufficient")
        engine, _, audit, _ = build(scenario)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "LLM"
        assert outcome.plan.escalation_required is True
        assert outcome.intents == []
        assert audit.events[0].intent_ids == []


class TestAllHealthy:
    def test_noop_produces_no_intent(self) -> None:
        scenario = load_scenario("all_healthy")
        engine, _, _, _ = build(scenario)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "LLM"
        assert outcome.plan.overall_risk == "LOW"
        assert outcome.intents == []
        assert outcome.forecast.total_shortfall_usdc == Decimal(0)


class TestSwapRequired:
    def test_slippage_is_clamped_and_intent_survives(self) -> None:
        scenario = load_scenario("swap_required")
        engine, _, _, _ = build(scenario)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "LLM"
        assert len(outcome.intents) == 1
        intent = outcome.intents[0]
        assert intent.type == "SWAP_THEN_TRANSFER"
        assert intent.max_slippage_bps == 50  # 요청 300 -> 정책 50
        assert intent.guard_verdict == "CLAMPED"
        assert intent.amount_usdc == Decimal(8000)  # payroll_exact
        assert any("SOL 소스" in note for note in intent.guard_notes)


class TestPromptInjection:
    def test_whole_plan_is_rejected_and_fallback_takes_over(self) -> None:
        scenario = load_scenario("prompt_injection")
        engine, _, audit, hook = build(scenario)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "FALLBACK"
        assert [a.kind for a in hook.alerts] == ["PROMPT_INJECTION"]
        registry = {w.address for w in scenario.context.wallets}
        assert {i.to_address for i in outcome.intents} <= registry
        assert audit.events[0].alerts


class TestInferenceBudgetLow:
    def test_provider_is_not_called_at_all(self) -> None:
        scenario = load_scenario("inference_budget_low")
        engine, provider, audit, _ = build(
            scenario,
            state=GatingState(inference_budget_days=scenario.inference_budget_days),
        )

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert provider.calls == []
        assert outcome.source == "FALLBACK"
        assert audit.events[0].skipped is True
        assert audit.events[0].skip_reason == "INFERENCE_BUDGET_LOW"
        # 예산이 없어도 급여는 나간다.
        assert outcome.intents


# --------------------------------------------------------------------------- #
# 사이클 간 동작
# --------------------------------------------------------------------------- #


class TestIdempotency:
    def test_same_context_twice_transfers_once(self) -> None:
        """수용 기준 — 동일 DecisionContext 2회 처리 시 이체는 1회."""
        scenario = load_scenario("normal_shortfall")
        engine, _, audit, _ = build(scenario)

        first = run(engine.run_cycle(scenario.context, scenario.now))
        second = run(engine.run_cycle(scenario.context, scenario.now))

        assert len(first.intents) == 2
        assert second.intents == []
        assert first.context_hash == second.context_hash
        assert first.decision_id == second.decision_id
        assert len(audit.events) == 2
        assert audit.events[1].skipped is True
        assert audit.events[1].skip_reason == "CONTEXT_UNCHANGED"

    def test_skip_still_writes_an_audit_record(self) -> None:
        scenario = load_scenario("normal_shortfall")
        engine, _, audit, _ = build(scenario)

        run(engine.run_cycle(scenario.context, scenario.now))
        run(engine.run_cycle(scenario.context, scenario.now))

        skipped = audit.events[1]
        assert skipped.skipped is True
        assert skipped.source == "CACHE"
        assert skipped.context_hash == audit.events[0].context_hash

    def test_generated_at_alone_does_not_change_the_intent_ids(self) -> None:
        """context_hash 에 시각이 섞여 있으면 멱등성이 통째로 무너진다."""
        scenario = load_scenario("normal_shortfall")
        engine, _, _, _ = build(scenario)

        first = run(engine.run_cycle(scenario.context, scenario.now))
        moved = scenario.context.model_copy(
            update={"generated_at": scenario.now + timedelta(minutes=30)}
        )
        second = run(engine.run_cycle(moved, scenario.now + timedelta(minutes=30)))

        assert first.context_hash == second.context_hash
        assert second.intents == []


class TestAuditTrail:
    @pytest.mark.parametrize(
        "name",
        [
            "normal_shortfall",
            "treasury_insufficient",
            "all_healthy",
            "swap_required",
            "prompt_injection",
            "inference_budget_low",
        ],
    )
    def test_every_cycle_is_recorded(self, name: str) -> None:
        scenario = load_scenario(name)
        engine, _, audit, _ = build(
            scenario,
            state=GatingState(inference_budget_days=scenario.inference_budget_days),
        )

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert len(audit.events) == 1
        event = audit.events[0]
        assert event.decision_id == outcome.decision_id
        assert event.context_hash == outcome.context_hash
        assert event.verdict == outcome.guard.verdict
        assert event.intent_ids == [i.intent_id for i in outcome.intents]
        # x402 자리는 B 세션까지 비어 있다.
        assert event.payment_signature is None
        assert event.cost_usdc is None


class TestGuardIsTheOnlyExit:
    @pytest.mark.parametrize(
        "name",
        [
            "normal_shortfall",
            "treasury_insufficient",
            "all_healthy",
            "swap_required",
            "prompt_injection",
            "inference_budget_low",
        ],
    )
    def test_outcome_intents_are_exactly_the_guard_output(self, name: str) -> None:
        """원칙 1 — 인텐트는 가드가 돌려준 리스트 그 자체다."""
        scenario = load_scenario(name)
        engine, _, _, _ = build(
            scenario,
            state=GatingState(inference_budget_days=scenario.inference_budget_days),
        )
        outcome = run(engine.run_cycle(scenario.context, scenario.now))
        assert outcome.intents is outcome.guard.intents

    def test_engine_module_never_constructs_an_intent(self) -> None:
        """엔진은 ExecutionIntent 를 만들지 않는다 — 가드에서만 나온다."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "treagent"
            / "judgment"
            / "engine.py"
        ).read_text(encoding="utf-8")
        assert "ExecutionIntent(" not in source
