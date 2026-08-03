"""LLM 공급자 계약.

여기 정의된 것은 **인터페이스뿐이다.** 실제 API 호출은 이 세션의 범위가 아니다.

구현체는 무엇을 내놓든 ``raw_text`` 하나만 책임진다. 그 문자열이
:class:`~treagent.judgment.models.DecisionPlan` 인지, 금액이 조작됐는지,
지갑이 실재하는지는 전부 :class:`~treagent.judgment.guard.PolicyGuard` 가 본다.
공급자에게 검증을 맡기면 공급자를 추가할 때마다 검증이 갈라진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderResult:
    """공급자 한 번의 호출 결과.

    ``payment_signature`` / ``cost_usdc`` 는 x402 결제 경로용 자리다.
    이번 세션에서는 항상 ``None`` 이며, 채우는 것은 B 세션의 몫이다.
    """

    raw_text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    payment_signature: str | None = None  # TODO(session-B): x402 결제 tx 서명
    cost_usdc: Decimal | None = None  # TODO(session-B): 실제 과금액
    degraded: bool = False
    """공급자가 정상 경로를 못 밟았다는 표시 (타임아웃 후 캐시 응답 등).

    ``True`` 면 오케스트레이터가 신뢰도를 낮춰 취급한다.
    """

    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """판단 계획을 생성하는 공급자.

    동기 시그니처인 것은 의도다. 구현체 대부분이 블로킹 HTTP 클라이언트를
    쓰게 되므로, 비동기 오케스트레이터는 ``asyncio.to_thread`` 로 감싸 부른다
    (``engine._call_provider`` 참고).
    """

    name: str

    def generate_plan(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: float = 10.0,
    ) -> ProviderResult: ...


class ProviderError(RuntimeError):
    """공급자가 응답을 만들지 못했다. 오케스트레이터는 폴백으로 넘어간다."""


__all__ = ["LLMProvider", "ProviderError", "ProviderResult"]
