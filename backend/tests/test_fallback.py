"""원칙 4 검증 — 공급자를 완전히 죽여도 파이프라인이 완주한다.

여기 있는 테스트가 하나라도 깨지면 "LLM 없이도 안전하게 돈다"는 주장이
성립하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

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
from treagent.judgment.providers.base import ProviderResult
from treagent.judgment.providers.mock import BreakMode, DeadProvider, MockProvider


def build_engine(
    scenario: Scenario,
    provider: Any = None,
    state: GatingState | None = None,
) -> tuple[JudgmentEngine, InMemoryAuditSink, CollectingAlertHook]:
    hook = CollectingAlertHook()
    guard = PolicyGuard(
        StubBalanceProvider(balances=balances_from_context(scenario.context)),
        alerts=hook,
    )
    audit = InMemoryAuditSink()
    engine = JudgmentEngine(
        guard, state=state or GatingState(), provider=provider, audit=audit
    )
    return engine, audit, hook


class TestNoProviderAtAll:
    def test_pipeline_completes_without_a_provider(self) -> None:
        scenario = load_scenario("normal_shortfall")
        engine, audit, _ = build_engine(scenario, provider=None)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "FALLBACK"
        assert outcome.intents, "폴백만으로도 집행 인텐트가 나와야 한다"
        assert all(i.amount_usdc > Decimal(0) for i in outcome.intents)
        assert len(audit.events) == 1

    def test_fallback_intents_are_fully_formed(self) -> None:
        scenario = load_scenario("normal_shortfall")
        engine, _, _ = build_engine(scenario)
        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        payroll = scenario.context.wallet("payroll")
        vault = scenario.context.wallet("vault")
        assert payroll is not None and vault is not None

        first = outcome.intents[0]
        assert first.from_address == vault.address
        assert first.to_address == payroll.address
        # payroll_exact 13500 -> 1회 한도 10000 으로 절삭
        assert first.amount_usdc == Decimal(10000)
        assert first.guard_verdict == "CLAMPED"
        assert first.max_slippage_bps == 50
        assert first.execute_after == scenario.now
        assert first.decision_id == outcome.decision_id
        assert first.intent_id


class TestDeadProvider:
    def test_exception_falls_back(self) -> None:
        scenario = load_scenario("normal_shortfall")
        provider = DeadProvider()
        engine, audit, _ = build_engine(scenario, provider=provider)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert provider.calls == 1  # 예외는 즉시 폴백 — 무의미한 재시도를 하지 않는다
        assert outcome.source == "FALLBACK"
        assert outcome.intents
        assert audit.events[0].source == "FALLBACK"

    @pytest.mark.parametrize(
        "mode",
        [BreakMode.MALFORMED_JSON, BreakMode.SCHEMA_VIOLATION, BreakMode.INJECTED_AMOUNT],
    )
    def test_unparseable_response_falls_back_after_one_retry(
        self, mode: BreakMode
    ) -> None:
        scenario = load_scenario("normal_shortfall")
        provider = MockProvider(scenario="normal_shortfall", break_mode=mode)
        engine, _, hook = build_engine(scenario, provider=provider)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert len(provider.calls) == 2, "스키마 실패는 1회만 재시도한다"
        assert outcome.source == "FALLBACK"
        assert outcome.intents
        assert any(a.kind == "SCHEMA_INVALID" for a in hook.alerts)

    def test_transient_failure_recovers_on_retry(self) -> None:
        """첫 호출만 죽는 경우는 예외 경로라 폴백으로 간다 (재시도는 스키마 전용)."""
        scenario = load_scenario("normal_shortfall")
        provider = MockProvider(scenario="normal_shortfall", fail_times=1)
        engine, _, _ = build_engine(scenario, provider=provider)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))
        assert outcome.source == "FALLBACK"
        assert outcome.intents


class TestPoisonedResponse:
    def test_unknown_wallet_is_rejected_then_fallback_runs(self) -> None:
        """인젝션이 성공해도 결정론적 폴백이 급여를 채운다.

        여기서 멈추면 인젝션 한 줄로 급여를 막을 수 있게 된다. 폴백은 온체인
        상태와 정책만으로 만들어지므로 오염이 흘러들 경로가 없다.
        """
        scenario = load_scenario("prompt_injection")
        provider = MockProvider(scenario="prompt_injection")
        engine, audit, hook = build_engine(scenario, provider=provider)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "FALLBACK"
        assert any(a.kind == "PROMPT_INJECTION" for a in hook.alerts)
        assert outcome.intents

        payroll = scenario.context.wallet("payroll")
        assert payroll is not None
        assert outcome.intents[0].to_address == payroll.address
        # 미등록 지갑 주소는 어디에도 남지 않는다.
        addresses = {i.to_address for i in outcome.intents} | {
            i.from_address for i in outcome.intents
        }
        registry = {w.address for w in scenario.context.wallets}
        assert addresses <= registry
        assert any("PROMPT_INJECTION" in a for a in audit.events[0].alerts)

    def test_bogus_option_drops_the_action_but_keeps_the_rest(self) -> None:
        scenario = load_scenario("normal_shortfall")
        provider = MockProvider(
            scenario="normal_shortfall", break_mode=BreakMode.BOGUS_OPTION
        )
        engine, _, _ = build_engine(scenario, provider=provider)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        # 계획 자체는 유효하므로 LLM 경로가 유지되고, 액션 하나만 사라진다.
        assert outcome.source == "LLM"
        assert outcome.guard.dropped_action_ids == ["act_fund_payroll"]
        assert [i.amount_usdc for i in outcome.intents] == [Decimal(3500)]

    def test_low_confidence_holds_instead_of_falling_back(self) -> None:
        """확신 없는 계획을 코드가 대신 밀어붙이지 않는다."""
        scenario = load_scenario("normal_shortfall")
        provider = MockProvider(
            scenario="normal_shortfall", break_mode=BreakMode.LOW_CONFIDENCE
        )
        engine, _, hook = build_engine(scenario, provider=provider)

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.guard.verdict == "HELD"
        assert outcome.intents == []
        assert any(a.kind == "LOW_CONFIDENCE" for a in hook.alerts)


class TestFallbackAcrossScenarios:
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
    def test_every_scenario_completes_with_a_dead_provider(self, name: str) -> None:
        scenario = load_scenario(name)
        engine, audit, _ = build_engine(scenario, provider=DeadProvider())

        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.source == "FALLBACK"
        assert outcome.guard.verdict in ("APPROVED", "CLAMPED")
        assert len(audit.events) == 1
        for intent in outcome.intents:
            assert intent.amount_usdc > Decimal(0)
            assert intent.max_slippage_bps <= scenario.context.policy.max_slippage_bps

    def test_insufficient_treasury_produces_no_intent(self) -> None:
        scenario = load_scenario("treasury_insufficient")
        engine, _, _ = build_engine(scenario, provider=DeadProvider())
        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert outcome.plan.escalation_required is True
        assert outcome.intents == []

    def test_healthy_treasury_produces_no_intent(self) -> None:
        scenario = load_scenario("all_healthy")
        engine, _, _ = build_engine(scenario, provider=DeadProvider())
        outcome = run(engine.run_cycle(scenario.context, scenario.now))

        assert [a.type for a in outcome.plan.actions] == ["NOOP"]
        assert outcome.intents == []


class TestProviderResultContract:
    def test_x402_fields_are_reserved_but_empty(self) -> None:
        """B 세션이 채울 자리가 계약에 이미 있다."""
        result = ProviderResult(raw_text="{}")
        assert result.payment_signature is None
        assert result.cost_usdc is None
        assert result.degraded is False
