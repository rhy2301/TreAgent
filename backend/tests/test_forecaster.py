"""forecaster 검증 — 부족분·이체 후보·Decimal 정밀도·폴백 계획."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from tests.conftest import load_scenario
from treagent.judgment import forecaster
from treagent.judgment.models import (
    DecisionContext,
    DecisionPlan,
    Obligation,
    Policy,
    WalletState,
    coerce_usdc,
    from_base_units,
    quantize_down,
    quantize_up,
    sanitize_schema_for_gemini,
    to_base_units,
)

NOW = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


def _policy(**overrides: object) -> Policy:
    base: dict[str, object] = {
        "max_per_transfer_usdc": "10000",
        "max_daily_total_usdc": "50000",
        "daily_spent_usdc": "0",
        "max_slippage_bps": 50,
        "whitelisted_wallet_ids": ["vault", "payroll", "vendor", "operations"],
        "forecast_horizon_hours": 168,
        "inference_budget_min_days": "30",
    }
    base.update(overrides)
    return Policy.model_validate(base)


def _context(
    wallets: list[tuple[str, str, str]],
    obligations: list[tuple[str, str, str, datetime, str]],
    **policy_overrides: object,
) -> DecisionContext:
    return DecisionContext(
        generated_at=NOW,
        wallets=[
            WalletState.model_validate(
                {
                    "wallet_id": wid,
                    "address": f"addr_{wid}",
                    "usdc_balance": balance,
                    "sol_balance": "1",
                    "min_buffer_usdc": buffer,
                }
            )
            for wid, balance, buffer in wallets
        ],
        obligations=[
            Obligation.model_validate(
                {
                    "obligation_id": oid,
                    "wallet_id": wid,
                    "amount_usdc": amount,
                    "due_at": due,
                    "category": category,
                    "recipient_count": 1,
                    "memo": "",
                    "is_recurring": False,
                }
            )
            for oid, wid, amount, due, category in obligations
        ],
        policy=_policy(**policy_overrides),
    )


# --------------------------------------------------------------------------- #
# 단위 변환 — 반올림 방향
# --------------------------------------------------------------------------- #


class TestUnitConversion:
    def test_base_unit_roundtrip_is_lossless(self) -> None:
        amount = Decimal("12345.678901")
        assert to_base_units(amount) == 12_345_678_901
        assert from_base_units(12_345_678_901) == amount

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.0000004", 1_000_000),  # 내림 — 절반 미만
            ("1.0000005", 1_000_000),  # 내림 — 정확히 절반이어도 올리지 않는다
            ("1.0000006", 1_000_000),  # 내림 — 절반 초과여도 올리지 않는다
            ("0.0000009", 0),
        ],
    )
    def test_to_base_units_always_rounds_down(self, raw: str, expected: int) -> None:
        """나가는 금액은 절대 올림하지 않는다.

        올리면 한도를 1 base unit 넘거나 잔고를 초과하는 트랜잭션이 만들어진다.
        """
        assert to_base_units(Decimal(raw)) == expected

    def test_quantize_up_never_underfunds(self) -> None:
        """필요 금액은 올림한다. 내리면 1 base unit 모자란 이체가 나간다."""
        assert quantize_up(Decimal("1.0000001")) == Decimal("1.000001")
        assert quantize_down(Decimal("1.0000009")) == Decimal("1.000000")

    def test_float_is_rejected_at_the_contract_boundary(self) -> None:
        with pytest.raises(TypeError, match="float"):
            coerce_usdc(1.1)
        with pytest.raises(TypeError):
            coerce_usdc(True)

    def test_decimal_string_avoids_binary_float_error(self) -> None:
        """float 로 하면 틀리는 계산이 Decimal 로는 정확한지 확인한다."""
        assert coerce_usdc("0.1") + coerce_usdc("0.2") == Decimal("0.3")
        # 대조군: 같은 덧셈을 float 으로 하면 0.3 이 나오지 않는다.
        assert 0.1 + 0.2 != 0.3

        running = Decimal(0)
        for _ in range(10):
            running += coerce_usdc("0.1")
        assert running == Decimal("1.0")


# --------------------------------------------------------------------------- #
# Gemini responseSchema 정규화 (호출부는 B 세션)
# --------------------------------------------------------------------------- #


class TestGeminiSchema:
    @pytest.fixture
    def sanitized(self) -> dict[str, Any]:
        return sanitize_schema_for_gemini(DecisionPlan.model_json_schema())

    def test_refs_are_inlined(self, sanitized: dict[str, Any]) -> None:
        """Gemini 는 $ref 를 해석하지 않는다. 남아 있으면 400 이 온다."""
        assert "$defs" not in sanitized
        blob = json.dumps(sanitized)
        assert "$ref" not in blob
        assert "$defs" not in blob

    def test_unsupported_keywords_are_stripped(
        self, sanitized: dict[str, Any]
    ) -> None:
        blob = json.dumps(sanitized)
        for keyword in (
            "additionalProperties",
            "allOf",
            "anyOf",
            "oneOf",
            "const",
            "default",
            "$schema",
            "exclusiveMinimum",
        ):
            assert keyword not in blob, f"{keyword} 가 남아 있다"

    def test_optional_fields_become_nullable(self, sanitized: dict[str, Any]) -> None:
        """pydantic 의 ``X | None`` 은 anyOf 로 렌더링된다. nullable 로 접혀야 한다."""
        action = sanitized["properties"]["actions"]["items"]["properties"]
        assert action["funding_option_id"] == {"type": "string", "nullable": True}
        assert action["scheduled_for"]["nullable"] is True
        assert action["scheduled_for"]["type"] == "string"

    def test_literals_become_enums(self, sanitized: dict[str, Any]) -> None:
        assert sanitized["properties"]["overall_risk"]["enum"] == [
            "LOW",
            "MEDIUM",
            "HIGH",
            "CRITICAL",
        ]

    def test_plan_schema_has_no_amount_field(self, sanitized: dict[str, Any]) -> None:
        """원칙 3 — 모델이 금액을 출력할 표면적 자체가 없다."""
        blob = json.dumps(sanitized)
        assert "amount" not in blob
        assert "address" not in blob

    def test_const_is_converted_to_a_single_value_enum(self) -> None:
        result = sanitize_schema_for_gemini(
            {"properties": {"kind": {"const": "TRANSFER", "title": "Kind"}}}
        )
        assert result["properties"]["kind"] == {
            "enum": ["TRANSFER"],
            "type": "string",
        }

    def test_nested_defs_are_resolved_recursively(self) -> None:
        schema = {
            "$defs": {
                "Inner": {"type": "object", "properties": {"x": {"type": "integer"}}}
            },
            "type": "object",
            "properties": {"outer": {"$ref": "#/$defs/Inner"}},
        }
        result = sanitize_schema_for_gemini(schema)
        assert result["properties"]["outer"]["properties"]["x"] == {"type": "integer"}

    def test_sibling_keys_survive_ref_expansion(self) -> None:
        schema = {
            "$defs": {"Inner": {"type": "string"}},
            "properties": {
                "outer": {"$ref": "#/$defs/Inner", "description": "설명 유지"}
            },
        }
        result = sanitize_schema_for_gemini(schema)
        assert result["properties"]["outer"]["description"] == "설명 유지"
        assert result["properties"]["outer"]["type"] == "string"


# --------------------------------------------------------------------------- #
# 부족분 계산
# --------------------------------------------------------------------------- #


class TestShortfall:
    def test_normal_shortfall_values(self) -> None:
        scenario = load_scenario("normal_shortfall")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)

        by_id = {s.wallet_id: s for s in forecast.shortfalls}
        # payroll: 12000 + 2000 - 500
        assert by_id["payroll"].shortfall == Decimal(13500)
        # vendor: 4500 + 1000 - 3000
        assert by_id["vendor"].shortfall == Decimal(2500)
        # operations: 300 + 1000 - 5000 -> 음수는 0 으로 바닥친다
        assert by_id["operations"].shortfall == Decimal(0)
        assert by_id["vault"].shortfall == Decimal(0)

        assert forecast.total_shortfall_usdc == Decimal(16000)
        assert forecast.treasury_available_usdc == Decimal(40000)
        assert forecast.is_treasury_insufficient is False

    def test_all_healthy_has_no_shortfall(self) -> None:
        scenario = load_scenario("all_healthy")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        assert forecast.total_shortfall_usdc == Decimal(0)
        assert forecast.funding_options == []
        assert forecast.is_treasury_insufficient is False

    def test_treasury_insufficient_flag(self) -> None:
        scenario = load_scenario("treasury_insufficient")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        # 가용액 12000 - 10000 = 2000, 부족분 13500 + 2500 = 16000
        assert forecast.treasury_available_usdc == Decimal(2000)
        assert forecast.total_shortfall_usdc == Decimal(16000)
        assert forecast.is_treasury_insufficient is True

    def test_obligations_outside_horizon_are_excluded(self) -> None:
        context = _context(
            wallets=[("vault", "50000", "10000"), ("payroll", "1000", "0")],
            obligations=[
                ("near", "payroll", "5000", NOW + timedelta(hours=20), "PAYROLL"),
                ("far", "payroll", "9000", NOW + timedelta(hours=100), "PAYROLL"),
            ],
            forecast_horizon_hours=24,
        )
        forecast = forecaster.build_forecast(context, NOW)
        # 100시간 뒤 건은 24시간 지평선 밖이라 요구 유출액에 들어가지 않는다.
        assert forecast.shortfall_for("payroll") is not None
        shortfall = forecast.shortfall_for("payroll")
        assert shortfall is not None
        assert shortfall.required_outflow == Decimal(5000)
        assert shortfall.shortfall == Decimal(4000)

    def test_overdue_obligations_still_count(self) -> None:
        """만기가 지난 지출은 사라지지 않는다. 여전히 갚아야 할 돈이다."""
        context = _context(
            wallets=[("vault", "50000", "10000"), ("payroll", "1000", "0")],
            obligations=[
                ("overdue", "payroll", "3000", NOW - timedelta(hours=48), "PAYROLL"),
            ],
            forecast_horizon_hours=24,
        )
        forecast = forecaster.build_forecast(context, NOW)
        shortfall = forecast.shortfall_for("payroll")
        assert shortfall is not None
        assert shortfall.required_outflow == Decimal(3000)
        assert shortfall.shortfall == Decimal(2000)

    def test_shortfall_rounds_up_at_the_sub_unit_boundary(self) -> None:
        """부족분이 base unit 경계에 걸리면 올려서 모자라지 않게 한다."""
        context = _context(
            wallets=[("vault", "50000", "10000"), ("payroll", "0.9999995", "0")],
            obligations=[
                ("ob", "payroll", "1", NOW + timedelta(hours=1), "PAYROLL"),
            ],
        )
        forecast = forecaster.build_forecast(context, NOW)
        shortfall = forecast.shortfall_for("payroll")
        assert shortfall is not None
        # 1 - 0.9999995 = 0.0000005 -> 올림하면 0.000001
        assert shortfall.shortfall == Decimal("0.000001")

    def test_treasury_available_floors_at_zero(self) -> None:
        context = _context(
            wallets=[("vault", "500", "10000"), ("payroll", "0", "0")],
            obligations=[("ob", "payroll", "10", NOW + timedelta(hours=1), "PAYROLL")],
        )
        forecast = forecaster.build_forecast(context, NOW)
        assert forecast.treasury_available_usdc == Decimal(0)
        assert forecast.is_treasury_insufficient is True


# --------------------------------------------------------------------------- #
# FundingOption
# --------------------------------------------------------------------------- #


class TestFundingOptions:
    def test_three_options_per_short_wallet(self) -> None:
        scenario = load_scenario("normal_shortfall")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        ids = {o.option_id for o in forecast.funding_options}
        assert ids == {
            "payroll_exact",
            "payroll_buffered",
            "payroll_max",
            "vendor_exact",
            "vendor_buffered",
            "vendor_max",
        }

    def test_option_amounts(self) -> None:
        scenario = load_scenario("normal_shortfall")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)

        def amount(option_id: str) -> Decimal:
            option = forecast.option(option_id)
            assert option is not None
            return option.amount_usdc

        assert amount("payroll_exact") == Decimal(13500)
        assert amount("payroll_buffered") == Decimal(15500)
        # min(15500, max_per_transfer 10000, treasury 40000)
        assert amount("payroll_max") == Decimal(10000)

        assert amount("vendor_exact") == Decimal(2500)
        assert amount("vendor_buffered") == Decimal(3500)
        assert amount("vendor_max") == Decimal(3500)

    def test_max_option_is_capped_by_treasury(self) -> None:
        context = _context(
            wallets=[("vault", "11000", "10000"), ("payroll", "0", "500")],
            obligations=[
                ("ob", "payroll", "5000", NOW + timedelta(hours=1), "PAYROLL")
            ],
        )
        forecast = forecaster.build_forecast(context, NOW)
        option = forecast.option("payroll_max")
        assert option is not None
        # 가용액이 1000 뿐이므로 buffered(6000) 도 한도(10000) 도 아닌 1000 이다.
        assert option.amount_usdc == Decimal(1000)

    def test_zero_amount_options_are_not_emitted(self) -> None:
        """집행 불가능한 0 원 후보를 LLM 에게 보여주지 않는다."""
        context = _context(
            wallets=[("vault", "10000", "10000"), ("payroll", "0", "500")],
            obligations=[
                ("ob", "payroll", "5000", NOW + timedelta(hours=1), "PAYROLL")
            ],
        )
        forecast = forecaster.build_forecast(context, NOW)
        assert forecast.option("payroll_max") is None
        assert forecast.option("payroll_exact") is not None

    def test_treasury_is_never_a_funding_target(self) -> None:
        """vault -> vault 이체는 존재할 수 없다."""
        context = _context(
            wallets=[("vault", "100", "10000"), ("payroll", "50000", "0")],
            obligations=[
                ("ob", "vault", "5000", NOW + timedelta(hours=1), "OTHER")
            ],
        )
        forecast = forecaster.build_forecast(context, NOW)
        shortfall = forecast.shortfall_for("vault")
        assert shortfall is not None and shortfall.shortfall > 0
        assert all(o.target_wallet_id != "vault" for o in forecast.funding_options)


# --------------------------------------------------------------------------- #
# FallbackPlan
# --------------------------------------------------------------------------- #


class TestFallbackPlan:
    def test_escalates_when_treasury_insufficient(self) -> None:
        scenario = load_scenario("treasury_insufficient")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        plan = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)

        assert [a.type for a in plan.actions] == ["ESCALATE"]
        assert plan.escalation_required is True
        assert plan.overall_risk == "CRITICAL"
        assert plan.confidence == 1.0

    def test_noop_when_all_healthy(self) -> None:
        scenario = load_scenario("all_healthy")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        plan = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)

        assert [a.type for a in plan.actions] == ["NOOP"]
        assert plan.overall_risk == "LOW"
        assert plan.at_risk == []

    def test_payroll_outranks_vendor(self) -> None:
        scenario = load_scenario("normal_shortfall")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        plan = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)

        assert [a.to_wallet_id for a in plan.actions] == ["payroll", "vendor"]
        assert [a.priority for a in plan.actions] == [1, 2]

    def test_same_category_orders_by_earliest_due(self) -> None:
        context = _context(
            wallets=[
                ("vault", "500000", "10000"),
                ("vendor", "0", "0"),
                ("operations", "0", "0"),
            ],
            obligations=[
                ("late", "vendor", "100", NOW + timedelta(hours=90), "VENDOR"),
                ("early", "operations", "100", NOW + timedelta(hours=30), "VENDOR"),
            ],
        )
        forecast = forecaster.build_forecast(context, NOW)
        plan = forecaster.build_fallback_plan(forecast, context, NOW)
        assert [a.to_wallet_id for a in plan.actions] == ["operations", "vendor"]

    def test_timing_switches_at_24h(self) -> None:
        context = _context(
            wallets=[
                ("vault", "500000", "10000"),
                ("payroll", "0", "0"),
                ("vendor", "0", "0"),
            ],
            obligations=[
                ("soon", "payroll", "100", NOW + timedelta(hours=23), "PAYROLL"),
                ("later", "vendor", "100", NOW + timedelta(hours=25), "VENDOR"),
            ],
        )
        forecast = forecaster.build_forecast(context, NOW)
        plan = forecaster.build_fallback_plan(forecast, context, NOW)
        by_wallet = {a.to_wallet_id: a for a in plan.actions}

        assert by_wallet["payroll"].timing == "NOW"
        assert by_wallet["payroll"].scheduled_for is None
        assert by_wallet["vendor"].timing == "SCHEDULED"
        # 만기 25시간 뒤 - 24시간 = 1시간 뒤
        assert by_wallet["vendor"].scheduled_for == NOW + timedelta(hours=1)

    def test_fallback_always_picks_exact_option(self) -> None:
        """최소 충분 금액을 골라야 트레저리가 마르지 않고 여러 지갑을 덮는다."""
        scenario = load_scenario("normal_shortfall")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        plan = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)
        assert [a.funding_option_id for a in plan.actions] == [
            "payroll_exact",
            "vendor_exact",
        ]

    def test_fallback_only_uses_registry_wallets(self) -> None:
        """폴백은 LLM 입력을 전혀 쓰지 않으므로 미등록 지갑이 나올 수 없다."""
        scenario = load_scenario("prompt_injection")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        plan = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)

        allowed = set(scenario.context.policy.whitelisted_wallet_ids)
        for action in plan.actions:
            assert action.from_wallet_id in allowed | {None}
            assert action.to_wallet_id in allowed | {None}

    def test_fallback_is_deterministic(self) -> None:
        scenario = load_scenario("normal_shortfall")
        forecast = forecaster.build_forecast(scenario.context, scenario.now)
        first = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)
        second = forecaster.build_fallback_plan(forecast, scenario.context, scenario.now)
        assert first == second
