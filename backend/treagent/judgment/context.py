"""ContextBuilder — 1~3단계 온체인 레이어와 판단 엔진 사이의 유일한 경계.

float 이 사는 곳은 이 파일뿐이다
--------------------------------
기존 ``chain/`` 레이어는 금액을 ``float`` 으로 주고받는다
(``chain.transfer.get_usdc_balance() -> float``). 판단 엔진은 ``Decimal`` 만
쓴다 (원칙 5). 두 규약이 만나는 지점을 여기 한 곳으로 몰아서, 변환이
어디서 일어나는지 grep 한 번으로 보이게 만든다.

변환은 반드시 ``Decimal(str(value))`` 다. ``Decimal(0.1)`` 은
0.1000000000000000055511151231257827021181583404541015625 를 만들지만
``Decimal(str(0.1))`` 은 정확히 ``Decimal("0.1")`` 이다.

``chain`` import 는 함수 안에서 한다. solana-py 나 ``.env`` 가 깨진 환경에서도
판단 엔진 테스트가 돌아야 하기 때문이며, 기존 ``services/solana_client.py`` 가
쓰는 것과 같은 방식이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from treagent.judgment.models import (
    DecisionContext,
    Obligation,
    ObligationCategory,
    Policy,
    WalletState,
    coerce_usdc,
)

logger = logging.getLogger(__name__)

# 기존 mock_data / schemas 의 PaymentType -> 판단 엔진의 카테고리.
_CATEGORY_MAP: dict[str, ObligationCategory] = {
    "payroll": "PAYROLL",
    "vendor": "VENDOR",
    "api_fee": "API_USAGE",
    "subscription": "API_USAGE",
}


def usdc_from_chain(value: object) -> Decimal:
    """체인/서비스 레이어의 ``float`` 금액을 ``Decimal`` 로 받는다.

    ``str()`` 을 거치는 것이 핵심이다. ``repr`` 은 float 을 되살릴 수 있는
    최단 십진 표현을 주므로, 사람이 의도한 값과 같아진다.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return coerce_usdc(str(value))
    return coerce_usdc(value)


def usdc_to_chain(amount: Decimal) -> float:
    """``Decimal`` 금액을 기존 체인 레이어가 받는 ``float`` 로 되돌린다.

    **집행 직전에만** 부른다. 여기서 정밀도가 깎이는 것은 기존 레이어의 제약이며,
    USDC 6자리 범위의 값은 float64 로 정확히 표현되므로 실무상 안전하다
    (2**53 base unit ≈ 90억 USDC 까지).

    TODO(session-B): ``chain.transfer`` 가 base unit 정수를 직접 받도록 열어
                     이 함수를 없애는 것이 최종 형태다.
    """
    return float(amount)


# --------------------------------------------------------------------------- #
# 잔고 공급자
# --------------------------------------------------------------------------- #


@runtime_checkable
class BalanceReader(Protocol):
    """지갑 잔고 조회. 실체는 ``chain.transfer`` 이거나 테스트 스텁이다."""

    async def get_usdc_balance(self, address: str) -> Decimal: ...


@dataclass
class ChainBalanceProvider:
    """가드 8번 검사용 실시간 잔고 조회기.

    **캐시를 반드시 버리고 조회한다.** ``chain.transfer`` 의 잔고 조회에는
    10초 TTL 캐시가 붙어 있는데(``CACHE_TTL = 10.0``), 그대로 쓰면 LLM 응답을
    기다린 1~3초 사이의 잔고 변동을 못 본다. 그러면 "서명 직전 재확인" 이라는
    검사의 존재 이유가 사라진다.
    """

    invalidate: bool = True

    async def get_usdc_balance(self, address: str) -> Decimal:
        from chain import transfer

        if self.invalidate:
            transfer.invalidate_cache(address)
        return usdc_from_chain(await transfer.get_usdc_balance(address))


# --------------------------------------------------------------------------- #
# 지출 공급자
# --------------------------------------------------------------------------- #


@runtime_checkable
class ObligationSource(Protocol):
    """예정 지출 공급자."""

    def list_obligations(self) -> list[Obligation]: ...


@dataclass
class StaticObligationSource:
    """이미 만들어진 목록을 그대로 준다 (테스트·데모용)."""

    obligations: list[Obligation]

    def list_obligations(self) -> list[Obligation]:
        return list(self.obligations)


@dataclass
class MockDataObligationSource:
    """기존 ``mock_data.PAYMENTS`` 를 :class:`Obligation` 으로 옮긴다.

    이미 지급 완료(``paid``)된 건은 제외한다. 남은 의무만 예측에 들어가야 한다.
    """

    exclude_statuses: frozenset[str] = frozenset({"paid"})

    def list_obligations(self) -> list[Obligation]:
        import mock_data

        return [
            payment_to_obligation(payment)
            for payment in mock_data.PAYMENTS
            if payment.get("status") not in self.exclude_statuses
        ]


def payment_to_obligation(payment: dict[str, Any]) -> Obligation:
    """기존 ``ScheduledPayment`` dict -> :class:`Obligation`."""
    return Obligation(
        obligation_id=str(payment["id"]),
        wallet_id=str(payment["source_wallet"]),
        amount_usdc=usdc_from_chain(payment["amount"]),
        due_at=_as_utc(payment["due_at"]),
        category=_CATEGORY_MAP.get(str(payment.get("type", "")), "OTHER"),
        recipient_count=max(1, int(payment.get("recipient_count", 1))),
        memo=str(payment.get("title", "")),
        is_recurring=bool(payment.get("is_recurring", False)),
    )


def _as_utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 정책 어댑터
# --------------------------------------------------------------------------- #


def policy_from_legacy(
    legacy: dict[str, Any],
    *,
    wallet_ids: list[str],
    forecast_horizon_hours: int = 168,
    inference_budget_min_days: str = "30",
) -> Policy:
    """기존 ``mock_data.POLICY`` dict -> :class:`Policy`.

    필드명이 다르다 (``max_single_transfer`` vs ``max_per_transfer_usdc`` 등).
    특히 화이트리스트는 **주소 목록에서 wallet_id 목록으로 바뀐다.** LLM 이
    주소를 다룰 일이 없어야 하므로(원칙 3), 화이트리스트도 id 공간에 있어야
    가드가 같은 언어로 검사할 수 있다.
    """
    return Policy(
        max_per_transfer_usdc=usdc_from_chain(legacy["max_single_transfer"]),
        max_daily_total_usdc=usdc_from_chain(legacy["max_daily_total"]),
        daily_spent_usdc=usdc_from_chain(legacy.get("daily_used", 0)),
        max_slippage_bps=int(legacy["max_slippage_bps"]),
        whitelisted_wallet_ids=list(wallet_ids),
        forecast_horizon_hours=forecast_horizon_hours,
        inference_budget_min_days=coerce_usdc(inference_budget_min_days),
    )


# --------------------------------------------------------------------------- #
# ContextBuilder
# --------------------------------------------------------------------------- #


@dataclass
class ContextBuilder:
    """온체인 상태 + 예정 지출 + 정책을 :class:`DecisionContext` 로 조립한다."""

    obligations: ObligationSource
    forecast_horizon_hours: int = 168
    inference_budget_min_days: str = "30"

    async def build(self, now: datetime | None = None) -> DecisionContext:
        """실제 온체인 잔고로 컨텍스트를 만든다.

        기존 3단계 인터페이스 두 개만 쓴다.
          - ``chain.wallet.WALLETS`` / ``get_address_str`` — 레지스트리와 주소
          - ``chain.transfer.get_all_balances()`` — 지갑 4개 잔고 병렬 조회

        ``get_all_balances`` 가 ``{wallet_id: {"usdc": float, "sol": float}}`` 를
        주므로, 여기서 전부 ``Decimal`` 로 바꾼다.
        """
        import mock_data
        from chain import transfer, wallet

        balances = await transfer.get_all_balances()
        wallets = [
            WalletState(
                wallet_id=wallet_id,
                address=wallet.get_address_str(wallet_id),
                usdc_balance=usdc_from_chain(balance["usdc"]),
                sol_balance=usdc_from_chain(balance["sol"]),
                min_buffer_usdc=usdc_from_chain(wallet.WALLETS[wallet_id].min_buffer),
            )
            for wallet_id, balance in sorted(balances.items())
        ]
        policy = policy_from_legacy(
            mock_data.get_policy(),
            wallet_ids=[w.wallet_id for w in wallets],
            forecast_horizon_hours=self.forecast_horizon_hours,
            inference_budget_min_days=self.inference_budget_min_days,
        )
        return DecisionContext(
            generated_at=now or datetime.now(UTC),
            wallets=wallets,
            obligations=self.obligations.list_obligations(),
            policy=policy,
        )

    def build_from_snapshot(
        self,
        *,
        balances: dict[str, dict[str, object]],
        addresses: dict[str, str],
        min_buffers: dict[str, object],
        policy: Policy,
        now: datetime,
    ) -> DecisionContext:
        """이미 조회한 잔고 스냅샷으로 컨텍스트를 만든다.

        온체인을 타지 않으므로 테스트와 재현 가능한 리플레이에 쓴다.
        """
        wallets = [
            WalletState(
                wallet_id=wallet_id,
                address=addresses[wallet_id],
                usdc_balance=usdc_from_chain(balance["usdc"]),
                sol_balance=usdc_from_chain(balance["sol"]),
                min_buffer_usdc=usdc_from_chain(min_buffers[wallet_id]),
            )
            for wallet_id, balance in sorted(balances.items())
        ]
        return DecisionContext(
            generated_at=now,
            wallets=wallets,
            obligations=self.obligations.list_obligations(),
            policy=policy,
        )


__all__ = [
    "BalanceReader",
    "ChainBalanceProvider",
    "ContextBuilder",
    "MockDataObligationSource",
    "ObligationSource",
    "StaticObligationSource",
    "payment_to_obligation",
    "policy_from_legacy",
    "usdc_from_chain",
    "usdc_to_chain",
]
