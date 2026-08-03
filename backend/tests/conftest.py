"""테스트 공용 픽스처.

여기서 네트워크를 건드리는 것은 아무것도 없다. 잔고 재조회는
:class:`StubBalanceProvider` 가 대신하며, LLM 응답은 시나리오 JSON 에서 온다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from treagent.judgment.models import DecisionContext, coerce_usdc

SCENARIO_DIR = Path(__file__).parent / "fixtures" / "scenarios"

SCENARIO_NAMES = [
    "normal_shortfall",
    "treasury_insufficient",
    "all_healthy",
    "swap_required",
    "prompt_injection",
    "inference_budget_low",
]


@dataclass(frozen=True)
class Scenario:
    """시나리오 JSON 한 건."""

    name: str
    now: datetime
    context: DecisionContext
    llm_response: dict[str, Any]
    inference_budget_days: Decimal | None

    @property
    def llm_response_text(self) -> str:
        return json.dumps(self.llm_response, ensure_ascii=False)


def load_scenario(name: str) -> Scenario:
    raw = json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))
    budget = raw.get("inference_budget_days")
    return Scenario(
        name=raw["name"],
        now=datetime.fromisoformat(raw["now"]),
        context=DecisionContext.model_validate(raw["context"]),
        llm_response=raw["llm_response"],
        inference_budget_days=None if budget is None else coerce_usdc(budget),
    )


@pytest.fixture(params=SCENARIO_NAMES)
def scenario(request: pytest.FixtureRequest) -> Scenario:
    """6개 시나리오를 하나씩 흘려보내는 파라미터화 픽스처."""
    return load_scenario(str(request.param))


@pytest.fixture
def normal() -> Scenario:
    return load_scenario("normal_shortfall")


@dataclass
class StubBalanceProvider:
    """가드 8번(서명 직전 잔고 재조회)의 테스트 대역.

    실제 어댑터는 ``chain.transfer.invalidate_cache`` 로 TTL 캐시를 버린 뒤
    RPC 를 다시 친다. 여기서는 그냥 딕셔너리를 본다.
    """

    balances: dict[str, Decimal] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def get_usdc_balance(self, address: str) -> Decimal:
        self.calls.append(address)
        return self.balances.get(address, Decimal(0))


def balances_from_context(context: DecisionContext) -> dict[str, Decimal]:
    """컨텍스트 잔고를 그대로 쓰는 스텁 초기값 (= 그 사이 변동 없음)."""
    return {w.address: w.usdc_balance for w in context.wallets}
