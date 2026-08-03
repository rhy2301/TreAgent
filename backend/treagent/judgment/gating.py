"""LLM 호출 트리거 판정과 계획 캐시.

왜 게이팅이 필요한가
--------------------
추론에는 돈이 든다 (x402 경로에서는 문자 그대로 USDC 가 나간다). 상태가
안 바뀌었는데 부르면 같은 답에 같은 값을 두 번 내는 것이다. 반대로 급여
만기가 24시간 안으로 들어오는 순간을 놓치면 판단 자체가 늦는다.

이 모듈은 **부를지 말지만 정한다.** 무엇을 할지는 여전히 예측기와 가드가 정하고,
호출을 건너뛰어도 파이프라인은 폴백으로 완주한다 (원칙 4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from treagent.judgment.models import (
    ZERO,
    DecisionContext,
    DecisionPlan,
    Forecast,
    Obligation,
    quantize_up,
)

BALANCE_DELTA_RATIO = Decimal("0.01")
"""잔고 변동 임계 비율 (1%)."""

BALANCE_DELTA_FLOOR = Decimal(100)
"""잔고 변동 임계 하한 (100 USDC). 잔고가 작은 지갑에서 1% 가 무의미해지는 것을 막는다."""

STALE_DECISION_AGE = timedelta(hours=6)
"""마지막 판단 이후 이만큼 지나면 다시 본다."""

IMMINENT_WINDOW = timedelta(hours=24)
"""만기 임박 구간."""

TriggerName = Literal[
    "BALANCE_MOVED",
    "OBLIGATION_CHANGED",
    "SHORTFALL_APPEARED",
    "OBLIGATION_IMMINENT",
    "DECISION_STALE",
    "SCHEDULED_ACTION_DUE",
]

SkipReason = Literal[
    "CONTEXT_UNCHANGED",
    "NO_TRIGGER",
    "INFERENCE_BUDGET_LOW",
]


def obligation_fingerprint(obligation: Obligation) -> str:
    """지출 한 건의 지문. 금액·만기·지갑·카테고리가 바뀌면 달라진다.

    ``memo`` 는 일부러 뺀다. 메모 문구 수정으로 추론 비용이 나가면 안 되고,
    인젝션 문구를 조금씩 바꿔가며 호출을 유도하는 것도 막을 수 있다.
    """
    parts = [
        obligation.obligation_id,
        obligation.wallet_id,
        format(obligation.amount_usdc.normalize(), "f"),
        obligation.due_at.isoformat(),
        obligation.category,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class GatingState:
    """사이클 사이에 살아남는 상태.

    이번 세션에서는 프로세스 메모리에만 있다.
    TODO(session-B): 감사 로그와 함께 영속화해 재시작 후에도 이어지게 한다.
    """

    last_context_hash: str | None = None
    last_plan: DecisionPlan | None = None
    last_decided_at: datetime | None = None
    last_balances: dict[str, Decimal] = field(default_factory=dict)
    last_fingerprints: dict[str, str] = field(default_factory=dict)
    last_positive_shortfalls: set[str] = field(default_factory=set)
    last_imminent: set[str] = field(default_factory=set)
    pending_scheduled: list[datetime] = field(default_factory=list)
    inference_budget_days: Decimal | None = None
    """남은 추론 예산(일). ``None`` 이면 알 수 없음 — 거부권을 행사하지 않는다.

    TODO(session-B): x402 잔고에서 실측값을 채운다.
    """

    def remember(
        self,
        context: DecisionContext,
        forecast: Forecast,
        plan: DecisionPlan,
        now: datetime,
        scheduled: list[datetime] | None = None,
    ) -> None:
        """이번 사이클 결과를 다음 사이클의 비교 기준으로 남긴다."""
        self.last_context_hash = context.context_hash()
        self.last_plan = plan
        self.last_decided_at = now
        self.last_balances = {w.wallet_id: w.usdc_balance for w in context.wallets}
        self.last_fingerprints = {
            o.obligation_id: obligation_fingerprint(o) for o in context.obligations
        }
        self.last_positive_shortfalls = {
            s.wallet_id for s in forecast.shortfalls if s.shortfall > ZERO
        }
        self.last_imminent = {
            o.obligation_id
            for o in context.obligations
            if o.due_at - now < IMMINENT_WINDOW
        }
        self.pending_scheduled = list(scheduled or [])


@dataclass(frozen=True)
class GatingDecision:
    """부를지 말지의 결론."""

    should_call: bool
    triggers: list[TriggerName]
    skip_reason: SkipReason | None = None
    cached_plan: DecisionPlan | None = None

    @property
    def skipped(self) -> bool:
        return not self.should_call


def evaluate(
    context: DecisionContext,
    forecast: Forecast,
    state: GatingState,
    now: datetime,
) -> GatingDecision:
    """트리거를 평가해 LLM 호출 여부를 정한다."""
    triggers: list[TriggerName] = []

    # ── 1. 잔고 변동 >= max(1%, 100 USDC) ─────────────────────────────
    for wallet in context.wallets:
        previous = state.last_balances.get(wallet.wallet_id)
        if previous is None:
            continue
        threshold = quantize_up(max(previous * BALANCE_DELTA_RATIO, BALANCE_DELTA_FLOOR))
        if abs(wallet.usdc_balance - previous) >= threshold:
            triggers.append("BALANCE_MOVED")
            break

    # ── 2. 신규/변경 obligation ──────────────────────────────────────
    current_fingerprints = {
        o.obligation_id: obligation_fingerprint(o) for o in context.obligations
    }
    if state.last_fingerprints and current_fingerprints != state.last_fingerprints:
        triggers.append("OBLIGATION_CHANGED")
    elif not state.last_fingerprints and current_fingerprints:
        # 첫 사이클. 비교 대상이 없으니 변경으로 본다.
        triggers.append("OBLIGATION_CHANGED")

    # ── 3. shortfall 이 0 -> 양수로 전이 ──────────────────────────────
    positive = {s.wallet_id for s in forecast.shortfalls if s.shortfall > ZERO}
    if positive - state.last_positive_shortfalls:
        triggers.append("SHORTFALL_APPEARED")

    # ── 4. obligation 이 24h 임박 구간 진입 ───────────────────────────
    imminent = {
        o.obligation_id
        for o in context.obligations
        if o.due_at - now < IMMINENT_WINDOW
    }
    if imminent - state.last_imminent:
        triggers.append("OBLIGATION_IMMINENT")

    # ── 5. 마지막 판단 이후 6h 경과 ──────────────────────────────────
    stale = (
        state.last_decided_at is not None
        and now - state.last_decided_at >= STALE_DECISION_AGE
    )
    if stale:
        triggers.append("DECISION_STALE")

    # ── 6. 직전 SCHEDULED 액션의 실행 시각 도래 ───────────────────────
    scheduled_due = any(due <= now for due in state.pending_scheduled)
    if scheduled_due:
        triggers.append("SCHEDULED_ACTION_DUE")

    # ── 스킵 판정 ────────────────────────────────────────────────────
    # 추론 예산 거부권이 가장 강하다. 예산이 바닥나면 무슨 트리거가 켜져도
    # 부르지 않는다 — 폴백만으로도 안전하게 완주하기 때문이다 (원칙 4).
    budget = state.inference_budget_days
    if budget is not None and budget < context.policy.inference_budget_min_days:
        return GatingDecision(
            should_call=False,
            triggers=triggers,
            skip_reason="INFERENCE_BUDGET_LOW",
            cached_plan=None,
        )

    unchanged = (
        state.last_context_hash is not None
        and state.last_context_hash == context.context_hash()
    )
    if unchanged and not scheduled_due:
        # 물질적 상태가 그대로다. 캐시된 계획을 그대로 쓴다.
        #
        # DECISION_STALE(6h) 은 여기서 **거부권을 뚫지 못한다.** 아무것도
        # 안 바뀐 상태를 6시간마다 다시 물어봐야 할 새 정보가 없고, 그
        # 호출은 순수한 비용이기 때문이다. 반대로 SCHEDULED_ACTION_DUE 는
        # 뚫는다 — 집행해야 할 시각이 실제로 도래한 것이라 행동이 필요하다.
        return GatingDecision(
            should_call=False,
            triggers=triggers,
            skip_reason="CONTEXT_UNCHANGED",
            cached_plan=state.last_plan,
        )

    if not triggers:
        return GatingDecision(
            should_call=False,
            triggers=[],
            skip_reason="NO_TRIGGER",
            cached_plan=state.last_plan,
        )

    return GatingDecision(should_call=True, triggers=triggers)


__all__ = [
    "BALANCE_DELTA_FLOOR",
    "BALANCE_DELTA_RATIO",
    "IMMINENT_WINDOW",
    "STALE_DECISION_AGE",
    "GatingDecision",
    "GatingState",
    "SkipReason",
    "TriggerName",
    "evaluate",
    "obligation_fingerprint",
]
