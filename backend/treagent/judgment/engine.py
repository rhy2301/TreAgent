"""JudgmentEngine — 한 판단 사이클의 오케스트레이터.

흐름 (분기와 무관하게 마지막은 항상 가드다)
-------------------------------------------
1. :func:`~treagent.judgment.forecaster.build_forecast` — 결정론적 산수
2. :func:`~treagent.judgment.gating.evaluate` — 부를지 말지
3. 공급자 호출 (선택) — 실패·스키마 오류는 폴백으로 흡수
4. :meth:`~treagent.judgment.guard.PolicyGuard.enforce` — **유일한 출구**
5. :class:`AuditSink` 기록

원칙 1 의 코드적 근거: 이 파일에서 ``ExecutionIntent`` 를 만드는 곳이 없다.
가져오지도 않는다. 가드가 돌려준 것을 그대로 실어 나를 뿐이다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from treagent.judgment import forecaster, gating, prompts
from treagent.judgment.gating import GatingDecision, GatingState
from treagent.judgment.guard import GuardResult, PolicyGuard
from treagent.judgment.models import (
    DecisionContext,
    DecisionPlan,
    ExecutionIntent,
    Forecast,
    sanitize_schema_for_gemini,
)
from treagent.judgment.providers.base import LLMProvider, ProviderResult

logger = logging.getLogger(__name__)

PlanSource = Literal["LLM", "FALLBACK", "CACHE"]

SCHEMA_RETRIES = 1
"""스키마 파싱 실패 시 재시도 횟수. 1회로 고정한다 — 두 번 틀린 모델은
세 번째에도 틀리고, 그동안 잔고는 계속 움직인다."""


# --------------------------------------------------------------------------- #
# 감사
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuditEvent:
    """사이클 하나의 기록."""

    at: datetime
    decision_id: str
    context_hash: str
    source: PlanSource
    skipped: bool
    skip_reason: str | None
    triggers: list[str]
    verdict: str
    intent_ids: list[str]
    notes: list[str]
    alerts: list[str]
    provider_name: str | None = None
    # TODO(session-B): x402 결제 tx 서명과 실제 과금액을 채운다.
    payment_signature: str | None = None
    cost_usdc: Decimal | None = None


@runtime_checkable
class AuditSink(Protocol):
    """감사 기록의 목적지.

    TODO(session-B): 파일/DB 영속화 + Solana memo 앵커 구현체를 추가한다.
                     엔진은 이 Protocol 만 알면 되므로 손대지 않는다.
    """

    def record(self, event: AuditEvent) -> None: ...


@dataclass
class InMemoryAuditSink:
    """리스트에 쌓는 기본 구현. 이번 세션에서는 이것뿐이다."""

    events: list[AuditEvent] = field(default_factory=list)

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


# --------------------------------------------------------------------------- #
# 결과
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CycleOutcome:
    """사이클 하나의 산출."""

    decision_id: str
    context_hash: str
    forecast: Forecast
    plan: DecisionPlan
    source: PlanSource
    guard: GuardResult
    gating: GatingDecision
    provider_result: ProviderResult | None = None

    @property
    def intents(self) -> list[ExecutionIntent]:
        """집행 대상. 가드를 통과한 것만 여기 있다."""
        return self.guard.intents

    @property
    def used_fallback(self) -> bool:
        return self.source == "FALLBACK"


# --------------------------------------------------------------------------- #
# 엔진
# --------------------------------------------------------------------------- #


class JudgmentEngine:
    """공급자가 있든 없든 같은 결과 타입을 낸다."""

    def __init__(
        self,
        guard: PolicyGuard,
        state: GatingState | None = None,
        provider: LLMProvider | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self._guard = guard
        self._state = state or GatingState()
        self._provider = provider
        self._audit: AuditSink = audit or InMemoryAuditSink()

    @property
    def state(self) -> GatingState:
        return self._state

    @property
    def audit(self) -> AuditSink:
        return self._audit

    async def run_cycle(
        self, context: DecisionContext, now: datetime | None = None
    ) -> CycleOutcome:
        """판단 사이클 한 번."""
        moment = now or datetime.now(timezone.utc)
        context_hash = context.context_hash()
        decision_id = f"dec_{context_hash[:16]}"

        forecast = forecaster.build_forecast(context, moment)
        decision = gating.evaluate(context, forecast, self._state, moment)

        plan, source, provider_result = await self._obtain_plan(
            decision, context, forecast, moment
        )

        guard_result = await self._guard.enforce(
            plan, context, forecast, moment, decision_id
        )

        # 가드가 계획 전체를 거부했다면 폴백으로 한 번 더 간다. 폴백은 온체인
        # 상태와 정책만으로 만들어지므로 오염된 LLM 출력이 여기 섞일 수 없고,
        # 거부를 이유로 멈춰버리면 인젝션 한 줄로 급여를 막을 수 있게 된다.
        if guard_result.rejected and source != "FALLBACK":
            logger.warning(
                "가드가 %s 계획을 거부했다 — 결정론적 폴백으로 재시도한다.", source
            )
            plan = forecaster.build_fallback_plan(forecast, context, moment)
            source = "FALLBACK"
            fallback_result = await self._guard.enforce(
                plan, context, forecast, moment, decision_id
            )
            guard_result = GuardResult(
                verdict=fallback_result.verdict,
                intents=fallback_result.intents,
                notes=guard_result.notes + fallback_result.notes,
                alerts=guard_result.alerts + fallback_result.alerts,
                dropped_action_ids=guard_result.dropped_action_ids
                + fallback_result.dropped_action_ids,
            )

        self._state.remember(
            context,
            forecast,
            plan,
            moment,
            scheduled=[
                intent.execute_after
                for intent in guard_result.intents
                if intent.execute_after > moment
            ],
        )

        self._audit.record(
            AuditEvent(
                at=moment,
                decision_id=decision_id,
                context_hash=context_hash,
                source=source,
                skipped=decision.skipped,
                skip_reason=decision.skip_reason,
                triggers=list(decision.triggers),
                verdict=guard_result.verdict,
                intent_ids=[i.intent_id for i in guard_result.intents],
                notes=list(guard_result.notes),
                alerts=[f"{a.kind}: {a.message}" for a in guard_result.alerts],
                provider_name=getattr(self._provider, "name", None),
                payment_signature=(
                    provider_result.payment_signature if provider_result else None
                ),
                cost_usdc=provider_result.cost_usdc if provider_result else None,
            )
        )

        return CycleOutcome(
            decision_id=decision_id,
            context_hash=context_hash,
            forecast=forecast,
            plan=plan,
            source=source,
            guard=guard_result,
            gating=decision,
            provider_result=provider_result,
        )

    # ---------------------------------------------------------------- #
    # 계획 확보
    # ---------------------------------------------------------------- #

    async def _obtain_plan(
        self,
        decision: GatingDecision,
        context: DecisionContext,
        forecast: Forecast,
        now: datetime,
    ) -> tuple[DecisionPlan, PlanSource, ProviderResult | None]:
        """계획을 얻는다. 어떤 경로로도 실패하지 않는다 — 최악이 폴백이다."""
        fallback = forecaster.build_fallback_plan(forecast, context, now)

        if decision.skipped:
            if decision.cached_plan is not None:
                # 캐시라도 가드를 다시 통과해야 한다. 캐시된 시점 이후에 잔고가
                # 변했을 수 있고, 8번 검사는 지금 값을 봐야 한다.
                return decision.cached_plan, "CACHE", None
            return fallback, "FALLBACK", None

        if self._provider is None:
            return fallback, "FALLBACK", None

        schema = sanitize_schema_for_gemini(DecisionPlan.model_json_schema())
        system, user = self._build_messages(context, forecast, now)

        result: ProviderResult | None = None
        for attempt in range(SCHEMA_RETRIES + 1):
            try:
                result = await self._call_provider(system, user, schema)
            # 공급자 예외는 종류를 가리지 않고 전부 흡수한다. 무엇이 터졌든
            # 결론은 같기 때문이다 — 폴백으로 간다. 여기서 예외를 좁게 잡으면
            # 예상 못 한 한 종류가 새어나가 사이클 전체가 죽고, 급여가 밀린다.
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "공급자 호출 실패 (시도 %d): %s — 폴백으로 넘어간다.",
                    attempt + 1,
                    exc,
                )
                return fallback, "FALLBACK", None

            parsed = self._guard.parse_plan(result.raw_text)
            if parsed is not None:
                return parsed, "LLM", result
            logger.warning("스키마 파싱 실패 (시도 %d).", attempt + 1)

        return fallback, "FALLBACK", result

    async def _call_provider(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> ProviderResult:
        """공급자를 별도 스레드에서 부른다.

        :class:`~treagent.judgment.providers.base.LLMProvider` 는 동기 계약이다.
        블로킹 HTTP 클라이언트를 이벤트 루프에서 그대로 부르면 잔고 조회를
        포함한 다른 코루틴이 전부 멈춘다.
        """
        assert self._provider is not None
        return await asyncio.to_thread(
            self._provider.generate_plan, system, user, schema, 10.0
        )

    @staticmethod
    def _build_messages(
        context: DecisionContext, forecast: Forecast, now: datetime
    ) -> tuple[str, str]:
        """공급자에게 넘길 (system, user) 쌍.

        조립은 전부 :mod:`treagent.judgment.prompts` 가 한다. 엔진이 프롬프트
        문자열을 알면 프롬프트를 고칠 때마다 오케스트레이터를 건드리게 된다.

        직렬화 원칙 하나는 여기서 못박는다. **금액은 FundingOption 으로만
        내보낸다.** 지갑 잔고와 부족분은 문맥으로 주되, 모델이 고를 수 있는
        것은 ``option_id`` 뿐이다.

        few-shot 은 (system, user) 계약에 담기지 않는다. 예시를 유저 메시지에
        이어 붙이면 그것도 '이번 입력' 처럼 보이기 때문이다. 공급자가
        :func:`~treagent.judgment.prompts.load_examples` 를 직접 읽어 자기
        SDK 의 멀티턴 형식으로 넣는다.
        """
        return prompts.load_system_prompt(), prompts.build_user_prompt(
            context, forecast, now
        )


__all__ = [
    "AuditEvent",
    "AuditSink",
    "CycleOutcome",
    "InMemoryAuditSink",
    "JudgmentEngine",
    "PlanSource",
]
