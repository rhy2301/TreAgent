"""픽스처 기반 공급자 — 테스트/CI 전용. 네트워크를 타지 않는다.

**정상 응답만 내는 것으로는 부족하다.** 가드의 10개 검사는 깨진 응답을
받아봐야 검증되므로, 이 공급자는 의도적으로 다음을 낼 수 있어야 한다.
  - 스키마 위반 (``BreakMode.MALFORMED_JSON`` / ``SCHEMA_VIOLATION``)
  - 미등록 wallet_id (``UNKNOWN_WALLET``)
  - 조작된 금액 필드 (``INJECTED_AMOUNT``)
  - 실재하지 않는 funding_option_id (``BOGUS_OPTION``)
  - 예외 (``RAISE``)
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from treagent.judgment.providers.base import ProviderError, ProviderResult

DEFAULT_FIXTURE_DIR = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "scenarios"
)


class BreakMode(str, Enum):
    """응답을 어떻게 망가뜨릴지."""

    NONE = "none"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_VIOLATION = "schema_violation"
    UNKNOWN_WALLET = "unknown_wallet"
    INJECTED_AMOUNT = "injected_amount"
    BOGUS_OPTION = "bogus_option"
    LOW_CONFIDENCE = "low_confidence"
    RAISE = "raise"


@dataclass
class MockProvider:
    """시나리오 이름으로 픽스처를 찾아 그대로 돌려준다."""

    scenario: str
    fixture_dir: Path = DEFAULT_FIXTURE_DIR
    break_mode: BreakMode = BreakMode.NONE
    name: str = "mock"
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_times: int = 0
    """앞의 N 번은 예외를 던진다. 재시도 경로 검증용."""

    def generate_plan(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: float = 10.0,
    ) -> ProviderResult:
        self.calls.append(
            {"system": system, "user": user, "schema": schema, "timeout_s": timeout_s}
        )

        if self.fail_times >= len(self.calls) or self.break_mode is BreakMode.RAISE:
            raise ProviderError(
                f"mock provider 가 의도적으로 실패했다 (호출 {len(self.calls)}회차)."
            )

        payload = self._load()
        raw_text = self._apply_break(payload)
        return ProviderResult(
            raw_text=raw_text,
            input_tokens=len(user) // 4,
            output_tokens=len(raw_text) // 4,
            latency_ms=1,
            payment_signature=None,
            cost_usdc=None,
            degraded=False,
        )

    # ------------------------------------------------------------------ #

    def _load(self) -> dict[str, Any]:
        path = self.fixture_dir / f"{self.scenario}.json"
        if not path.is_file():
            raise ProviderError(f"시나리오 픽스처를 찾을 수 없다: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        response = raw.get("llm_response")
        if not isinstance(response, dict):
            raise ProviderError(f"{path} 에 llm_response 가 없다.")
        return copy.deepcopy(response)

    def _apply_break(self, payload: dict[str, Any]) -> str:
        mode = self.break_mode
        if mode is BreakMode.NONE:
            return json.dumps(payload, ensure_ascii=False)

        if mode is BreakMode.MALFORMED_JSON:
            # 닫는 괄호를 잘라낸다. 스트리밍이 잘린 응답과 같은 모양이다.
            return json.dumps(payload, ensure_ascii=False)[:-12]

        if mode is BreakMode.SCHEMA_VIOLATION:
            payload["overall_risk"] = "정말로_위험함"
            return json.dumps(payload, ensure_ascii=False)

        actions = payload.get("actions") or []
        if actions:
            if mode is BreakMode.UNKNOWN_WALLET:
                actions[0]["to_wallet_id"] = "attacker_wallet"
            elif mode is BreakMode.INJECTED_AMOUNT:
                # 계약에 없는 필드. extra="forbid" 가 파싱 단계에서 잡아야 한다.
                actions[0]["amount_usdc"] = "999999.999999"
            elif mode is BreakMode.BOGUS_OPTION:
                actions[0]["funding_option_id"] = "payroll_infinite"
        if mode is BreakMode.LOW_CONFIDENCE:
            payload["confidence"] = 0.2

        return json.dumps(payload, ensure_ascii=False)


@dataclass
class DeadProvider:
    """무슨 일이 있어도 예외를 던진다. 원칙 4 의 검증용."""

    name: str = "dead"
    calls: int = 0

    def generate_plan(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: float = 10.0,
    ) -> ProviderResult:
        self.calls += 1
        raise ProviderError("공급자가 완전히 죽었다.")


__all__ = ["BreakMode", "DeadProvider", "MockProvider"]
