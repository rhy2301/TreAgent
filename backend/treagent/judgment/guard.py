"""PolicyGuard — LLM 제안서를 집행 가능한 인텐트로 바꾸는 유일한 관문.

이 모듈의 존재 이유
-------------------
LLM 출력은 제안서다. :class:`~treagent.judgment.models.ExecutionIntent` 는
**이 파일 안에서만** 만들어진다. 그래서 인텐트가 존재한다는 것은 곧 아래 10개
검사를 전부 통과했다는 뜻이 된다 (원칙 1).

클램프와 거부의 구분
--------------------
- **CLAMPED** — 한도를 넘었다. 잘라서 집행한다. 급여를 절반만 채우는 것이
  아예 안 채우는 것보다 낫다.
- **REJECTED** — 미등록 지갑, 스키마 불일치, 트레저리 일관성 위반.
  계획 자체를 신뢰할 수 없으므로 전면 중단하고 결정론적 폴백으로 넘긴다.

이 둘을 섞으면 둘 다 무의미해진다. 한도 초과를 거부하면 정상 운영이 멈추고,
미등록 주소를 클램프하면 자금이 나간다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import ValidationError

from treagent.judgment.models import (
    FUNDING_REQUIRED_ACTIONS,
    ZERO,
    DecisionContext,
    DecisionPlan,
    ExecutionIntent,
    Forecast,
    GuardVerdict,
    ProposedAction,
    make_intent_id,
    quantize_down,
)

logger = logging.getLogger(__name__)

DEDUPE_WINDOW = timedelta(hours=24)
"""``intent_id`` 중복 판정 구간. 이 안에서 같은 키는 두 번 나가지 않는다."""

STALE_SCHEDULE_TOLERANCE = timedelta(hours=24)
"""과거 ``scheduled_for`` 를 NOW 로 승격할 수 있는 상한. 근거는
:meth:`PolicyGuard._resolve_execute_after` 주석에 적었다."""

LOW_CONFIDENCE_THRESHOLD = 0.6
"""이 미만이면 집행하지 않고 사람에게 넘긴다."""


AlertKind = Literal[
    "SCHEMA_INVALID",
    "PROMPT_INJECTION",
    "TREASURY_INCONSISTENT",
    "LOW_CONFIDENCE",
]

PlanVerdict = Literal["APPROVED", "CLAMPED", "REJECTED", "HELD"]


@dataclass(frozen=True)
class GuardAlert:
    """사람이 봐야 하는 사건."""

    kind: AlertKind
    message: str


@dataclass(frozen=True)
class GuardResult:
    """가드 판정 결과.

    ``verdict`` 는 계획 전체의 결론이고, 개별 인텐트의 ``guard_verdict`` 와는
    다르다. 예를 들어 액션 하나만 클램프됐으면 계획 판정도 CLAMPED 다.
    """

    verdict: PlanVerdict
    intents: list[ExecutionIntent]
    notes: list[str]
    alerts: list[GuardAlert]
    dropped_action_ids: list[str]

    @property
    def rejected(self) -> bool:
        """계획을 신뢰할 수 없어 폴백으로 넘겨야 하는가."""
        return self.verdict == "REJECTED"

    @property
    def held(self) -> bool:
        """집행 보류 — 사람 확인 대기."""
        return self.verdict == "HELD"


# --------------------------------------------------------------------------- #
# 의존성 Protocol — 가드 자체는 네트워크도 저장소도 모른다
# --------------------------------------------------------------------------- #


@runtime_checkable
class BalanceProvider(Protocol):
    """8번 검사(서명 직전 잔고 재조회)의 공급자.

    **구현체는 캐시를 반드시 무효화한 뒤 조회해야 한다.**
    ``chain.transfer`` 의 잔고 조회에는 10초 TTL 캐시가 붙어 있다. 그대로 쓰면
    LLM 응답을 기다린 1~3초 사이의 변동을 못 보고, 이 검사가 무의미해진다.
    :class:`~treagent.judgment.context.ChainBalanceProvider` 를 참고할 것.
    """

    async def get_usdc_balance(self, address: str) -> Decimal: ...


@runtime_checkable
class IntentLedger(Protocol):
    """9번 검사(멱등성)의 원장."""

    def is_recent(self, intent_id: str, now: datetime) -> bool: ...

    def record(self, intent_id: str, now: datetime) -> None: ...


@runtime_checkable
class AlertHook(Protocol):
    """10번 검사가 부르는 알림 훅."""

    def notify(self, alert: GuardAlert) -> None: ...


@dataclass
class InMemoryIntentLedger:
    """프로세스 메모리 원장.

    프로세스가 죽으면 기억도 죽는다. 실제 재시작 내성은 이 Protocol 을
    영속 저장소로 구현해야 얻어진다 — B 세션의 감사 로그와 함께 붙인다.
    """

    seen: dict[str, datetime] = field(default_factory=dict)

    def is_recent(self, intent_id: str, now: datetime) -> bool:
        recorded = self.seen.get(intent_id)
        if recorded is None:
            return False
        if now - recorded >= DEDUPE_WINDOW:
            del self.seen[intent_id]
            return False
        return True

    def record(self, intent_id: str, now: datetime) -> None:
        self.seen[intent_id] = now


@dataclass
class CollectingAlertHook:
    """알림을 리스트에 모으는 기본 훅."""

    alerts: list[GuardAlert] = field(default_factory=list)

    def notify(self, alert: GuardAlert) -> None:
        self.alerts.append(alert)
        logger.warning("가드 경보 [%s] %s", alert.kind, alert.message)


# --------------------------------------------------------------------------- #
# 내부 작업 단위
# --------------------------------------------------------------------------- #


@dataclass
class _Candidate:
    """검사를 통과해 나가는 중인 액션 하나."""

    action: ProposedAction
    amount: Decimal
    slippage_bps: int
    notes: list[str] = field(default_factory=list)
    clamped: bool = False


# --------------------------------------------------------------------------- #
# PolicyGuard
# --------------------------------------------------------------------------- #


class PolicyGuard:
    """10개 검사를 표에 적힌 순서 그대로 수행한다."""

    def __init__(
        self,
        balances: BalanceProvider,
        ledger: IntentLedger | None = None,
        alerts: AlertHook | None = None,
    ) -> None:
        self._balances = balances
        self._ledger: IntentLedger = ledger or InMemoryIntentLedger()
        self._alerts: AlertHook = alerts or CollectingAlertHook()

    # ---------------------------------------------------------------- #
    # 1. 스키마 파싱
    # ---------------------------------------------------------------- #

    def parse_plan(self, raw_text: str) -> DecisionPlan | None:
        """LLM 원문을 :class:`DecisionPlan` 으로 파싱한다. 실패하면 ``None``.

        재시도 여부는 여기서 정하지 않는다. 오케스트레이터(``engine``)가
        1회 재시도 후에도 ``None`` 이면 폴백으로 넘긴다.

        ``extra="forbid"`` 덕분에 모델이 계약에 없는 필드(예: ``amount_usdc``)를
        끼워 넣으면 조용히 무시되지 않고 여기서 걸린다.
        """
        try:
            return DecisionPlan.model_validate_json(raw_text)
        except ValidationError as exc:
            self._alerts.notify(
                GuardAlert(
                    kind="SCHEMA_INVALID",
                    message=f"DecisionPlan 스키마 파싱 실패: {exc.error_count()}건",
                )
            )
            logger.warning("스키마 파싱 실패: %s", exc)
            return None
        except ValueError as exc:  # JSON 자체가 깨진 경우
            self._alerts.notify(
                GuardAlert(kind="SCHEMA_INVALID", message=f"JSON 파싱 실패: {exc}")
            )
            return None

    # ---------------------------------------------------------------- #
    # 2 ~ 10
    # ---------------------------------------------------------------- #

    async def enforce(
        self,
        plan: DecisionPlan,
        context: DecisionContext,
        forecast: Forecast,
        now: datetime,
        decision_id: str,
    ) -> GuardResult:
        """계획을 검사하고 집행 가능한 인텐트만 남긴다."""
        notes: list[str] = []
        alerts: list[GuardAlert] = []
        dropped: list[str] = []
        policy = context.policy
        context_hash = context.context_hash()

        registry = {w.wallet_id: w for w in context.wallets}
        whitelist = set(policy.whitelisted_wallet_ids)

        # ── 2. 모든 wallet_id 가 화이트리스트에 있는가 ──────────────────
        # 실패 시 **전체 거부**. 미등록 주소가 하나라도 섞였다는 것은 계획을
        # 만든 쪽이 오염됐다는 뜻이고, 나머지 액션도 신뢰할 수 없다.
        unknown = self._unknown_wallets(plan, whitelist, registry)
        if unknown:
            alert = GuardAlert(
                kind="PROMPT_INJECTION",
                message=(
                    f"화이트리스트에 없는 wallet_id 가 계획에 있다: {sorted(unknown)}. "
                    "프롬프트 인젝션 가능성 — 계획 전체를 거부한다."
                ),
            )
            self._alerts.notify(alert)
            return GuardResult(
                verdict="REJECTED",
                intents=[],
                notes=[alert.message],
                alerts=[alert],
                dropped_action_ids=[a.action_id for a in plan.actions],
            )

        # ── 4. is_treasury_insufficient <-> ESCALATE 일관성 ────────────
        # 2번 직후에 둔 이유: 3번은 액션 단위 폐기라 계획 전체 판정을 바꾸지
        # 않지만, 4번은 전체 거부라 개별 액션을 손대기 전에 끝내야 한다.
        inconsistency = self._treasury_inconsistency(plan, forecast)
        if inconsistency is not None:
            alert = GuardAlert(kind="TREASURY_INCONSISTENT", message=inconsistency)
            self._alerts.notify(alert)
            return GuardResult(
                verdict="REJECTED",
                intents=[],
                notes=[inconsistency],
                alerts=[alert],
                dropped_action_ids=[a.action_id for a in plan.actions],
            )

        # ── 3. funding_option_id 실재 + 대상 일치 ─────────────────────
        candidates: list[_Candidate] = []
        for action in plan.actions:
            if action.type not in FUNDING_REQUIRED_ACTIONS:
                # NOOP / ESCALATE 는 자금을 움직이지 않으므로 인텐트가 없다.
                continue

            option = (
                forecast.option(action.funding_option_id)
                if action.funding_option_id
                else None
            )
            if option is None:
                dropped.append(action.action_id)
                notes.append(
                    f"[3] {action.action_id}: funding_option_id "
                    f"{action.funding_option_id!r} 가 예측에 없다 — 폐기."
                )
                continue

            # 옵션의 대상과 액션의 수신 지갑이 다르면, payroll 몫으로 계산된
            # 금액이 vendor 로 흘러간다. 금액과 목적지를 분리해 지정할 수
            # 있게 두면 그 자체가 우회로가 된다.
            if option.target_wallet_id != action.to_wallet_id:
                dropped.append(action.action_id)
                notes.append(
                    f"[3] {action.action_id}: 옵션 대상({option.target_wallet_id})과 "
                    f"수신 지갑({action.to_wallet_id})이 다르다 — 폐기."
                )
                continue

            if action.from_wallet_id is None or action.from_wallet_id not in registry:
                dropped.append(action.action_id)
                notes.append(
                    f"[3] {action.action_id}: 출금 지갑을 특정할 수 없다 — 폐기."
                )
                continue

            # **금액은 언제나 여기서 온다.** 입력 계획에 금액 필드가 실려 와도
            # (extra=forbid 로 애초에 파싱되지 않지만) 쓰지 않는다.
            candidates.append(
                _Candidate(
                    action=action,
                    amount=option.amount_usdc,
                    slippage_bps=policy.max_slippage_bps,
                )
            )

        # ── 5. 1회 이체 한도 -> CLAMPED ───────────────────────────────
        for candidate in candidates:
            if candidate.amount > policy.max_per_transfer_usdc:
                original = candidate.amount
                candidate.amount = quantize_down(policy.max_per_transfer_usdc)
                candidate.clamped = True
                candidate.notes.append(
                    f"[5] 1회 한도 초과 {original} -> {candidate.amount} USDC 로 절삭."
                )

        # ── 6. 일일 누적 한도 -> 낮은 우선순위부터 절삭 ────────────────
        # priority 는 1 이 가장 높다. 급한 것부터 채우고 남는 여력을 뒤로 넘긴다.
        candidates.sort(key=lambda c: (c.action.priority, c.action.action_id))
        headroom = policy.daily_headroom_usdc
        surviving: list[_Candidate] = []
        for candidate in candidates:
            if headroom <= ZERO:
                dropped.append(candidate.action.action_id)
                notes.append(
                    f"[6] {candidate.action.action_id}: 일일 한도 여력이 없다 "
                    f"(사용 {policy.daily_spent_usdc} / 한도 "
                    f"{policy.max_daily_total_usdc}) — 폐기."
                )
                continue
            if candidate.amount > headroom:
                original = candidate.amount
                candidate.amount = quantize_down(headroom)
                candidate.clamped = True
                candidate.notes.append(
                    f"[6] 일일 한도 잔여 {headroom} USDC 에 맞춰 "
                    f"{original} -> {candidate.amount} 로 절삭."
                )
            if candidate.amount <= ZERO:
                dropped.append(candidate.action.action_id)
                notes.append(
                    f"[6] {candidate.action.action_id}: 절삭 후 금액이 0 이다 — 폐기."
                )
                continue
            headroom -= candidate.amount
            surviving.append(candidate)
        candidates = surviving

        # ── 7. 슬리피지 클램프 ────────────────────────────────────────
        for candidate in candidates:
            requested = candidate.action.max_slippage_bps
            if requested is None:
                candidate.slippage_bps = policy.max_slippage_bps
            elif requested > policy.max_slippage_bps:
                candidate.slippage_bps = policy.max_slippage_bps
                candidate.clamped = True
                candidate.notes.append(
                    f"[7] 슬리피지 {requested}bps 요청 -> 정책값 "
                    f"{policy.max_slippage_bps}bps 로 클램프."
                )
            else:
                candidate.slippage_bps = requested

        # ── 8. 소스 지갑 잔고 실시간 재조회 ───────────────────────────
        # LLM 응답을 기다린 1~3초 사이에 잔고가 변할 수 있다. 서명 직전
        # 재확인은 금융 시스템의 기본이다.
        reserved: dict[str, Decimal] = {}
        live_cache: dict[str, Decimal] = {}
        intents: list[ExecutionIntent] = []

        for candidate in candidates:
            action = candidate.action
            source_id = action.from_wallet_id
            assert source_id is not None  # 3번에서 걸러졌다
            source = registry[source_id]

            if action.source_token == "SOL":
                # SOL -> USDC 스왑 경로. 소스의 USDC 잔고는 근거가 되지 못하고,
                # 필요한 SOL 양은 스왑 견적이 있어야 나온다. chain/swap.py 는
                # 아직 스텁이므로 여기서는 SOL 보유 여부만 확인하고, 실제
                # 견적 대조는 집행 직전으로 미룬다.
                # TODO(session-B): Jupiter 견적으로 필요 SOL 을 산출해 재검증.
                if source.sol_balance <= ZERO:
                    dropped.append(action.action_id)
                    notes.append(
                        f"[8] {action.action_id}: 스왑 소스 {source_id} 에 SOL 이 없다 — 폐기."
                    )
                    continue
                candidate.notes.append(
                    "[8] SOL 소스 — USDC 잔고 검사를 생략했다. "
                    "스왑 견적 대조는 집행 직전에 수행해야 한다."
                )
            else:
                if source.address not in live_cache:
                    live_cache[source.address] = await self._balances.get_usdc_balance(
                        source.address
                    )
                live = live_cache[source.address]
                # 소스의 최소 버퍼도 지킨다. 예측 단계에서 이미 반영했지만,
                # 잔고가 그 사이 줄었다면 여기가 마지막 방어선이다.
                available = quantize_down(
                    live - source.min_buffer_usdc - reserved.get(source_id, ZERO)
                )
                if available < candidate.amount:
                    dropped.append(action.action_id)
                    notes.append(
                        f"[8] {action.action_id}: {source_id} 실시간 가용액 "
                        f"{available} USDC < 요청 {candidate.amount} USDC — 폐기."
                    )
                    continue
                reserved[source_id] = reserved.get(source_id, ZERO) + candidate.amount

            # scheduled_for 처리 (근거는 _resolve_execute_after 주석 참조)
            execute_after = self._resolve_execute_after(action, now, candidate.notes)
            if execute_after is None:
                dropped.append(action.action_id)
                notes.append(
                    f"[8] {action.action_id}: scheduled_for 가 허용 범위를 "
                    "벗어나 폐기했다."
                )
                # 예약한 자금은 되돌린다.
                if action.source_token != "SOL":
                    reserved[source_id] = reserved.get(source_id, ZERO) - candidate.amount
                continue

            # ── 9. intent_id 24시간 중복 -> 스킵 (멱등) ────────────────
            intent_id = make_intent_id(context_hash, action.action_id)
            if self._ledger.is_recent(intent_id, now):
                notes.append(
                    f"[9] {action.action_id}: intent_id 가 24시간 안에 이미 "
                    "처리됐다 — 스킵 (멱등)."
                )
                if action.source_token != "SOL":
                    reserved[source_id] = reserved.get(source_id, ZERO) - candidate.amount
                continue

            verdict: GuardVerdict = "CLAMPED" if candidate.clamped else "APPROVED"
            target = registry[str(action.to_wallet_id)]
            intents.append(
                ExecutionIntent(
                    intent_id=intent_id,
                    decision_id=decision_id,
                    type=action.type,
                    from_address=source.address,
                    to_address=target.address,
                    amount_usdc=candidate.amount,
                    max_slippage_bps=candidate.slippage_bps,
                    execute_after=execute_after,
                    guard_verdict=verdict,
                    guard_notes=list(candidate.notes),
                )
            )

        # ── 10. confidence < 0.6 -> 집행 보류 + 알림 ──────────────────
        if plan.confidence < LOW_CONFIDENCE_THRESHOLD:
            alert = GuardAlert(
                kind="LOW_CONFIDENCE",
                message=(
                    f"confidence {plan.confidence} < {LOW_CONFIDENCE_THRESHOLD} — "
                    f"인텐트 {len(intents)}건의 집행을 보류하고 사람에게 넘긴다."
                ),
            )
            self._alerts.notify(alert)
            alerts.append(alert)
            notes.append(alert.message)
            # 폴백으로 대체하지 않는다. 폴백은 confidence 1.0 이므로 여기에
            # 걸릴 수 없고, 걸렸다는 것은 LLM 이 스스로 확신하지 못한다는 뜻이다.
            # 그 판단을 코드가 뒤집는 대신 사람이 보게 한다.
            return GuardResult(
                verdict="HELD",
                intents=[],
                notes=notes,
                alerts=alerts,
                dropped_action_ids=dropped,
            )

        # 여기까지 온 것만 원장에 남긴다.
        # TODO(session-B): 집행 확정 시점에 원장을 갱신하도록 executor 와 분리.
        #                  지금은 인텐트 발행 시점에 기록해 재처리를 막는다.
        for intent in intents:
            self._ledger.record(intent.intent_id, now)

        plan_verdict: PlanVerdict = (
            "CLAMPED"
            if any(i.guard_verdict == "CLAMPED" for i in intents)
            else "APPROVED"
        )
        return GuardResult(
            verdict=plan_verdict,
            intents=intents,
            notes=notes,
            alerts=alerts,
            dropped_action_ids=dropped,
        )

    # ---------------------------------------------------------------- #
    # 헬퍼
    # ---------------------------------------------------------------- #

    @staticmethod
    def _unknown_wallets(
        plan: DecisionPlan,
        whitelist: set[str],
        registry: Mapping[str, object],
    ) -> set[str]:
        """계획에 등장하는 모든 wallet_id 중 허용되지 않은 것.

        자금을 움직이지 않는 액션(NOOP/ESCALATE)의 필드도 함께 본다. 인젝션은
        결과가 아니라 시도 자체가 신호이므로, 집행되지 않을 자리에 심어진
        주소도 놓치면 안 된다.
        """
        referenced = {
            wallet_id
            for action in plan.actions
            for wallet_id in (action.from_wallet_id, action.to_wallet_id)
            if wallet_id is not None
        }
        return {
            wallet_id
            for wallet_id in referenced
            if wallet_id not in whitelist or wallet_id not in registry
        }

    @staticmethod
    def _treasury_inconsistency(plan: DecisionPlan, forecast: Forecast) -> str | None:
        """4번 검사. 위반이면 사유 문자열, 정상이면 ``None``.

        양방향으로 본다.
          - 자금이 모자란데 ESCALATE 가 없다 -> 없는 돈을 쓰겠다는 계획이다.
          - 자금이 있는데 ESCALATE 로 때운다 -> 채울 수 있는 부족분을 방치한다.
            단, 부족분 자체가 없으면 무해하므로 통과시킨다.
        """
        has_escalate = any(a.type == "ESCALATE" for a in plan.actions)

        if forecast.is_treasury_insufficient and not has_escalate:
            return (
                f"[4] 트레저리 가용액({forecast.treasury_available_usdc})이 총 부족분"
                f"({forecast.total_shortfall_usdc})보다 작은데 ESCALATE 가 없다 "
                "— 계획 전체 거부."
            )

        has_shortfall = any(s.shortfall > ZERO for s in forecast.shortfalls)
        if not forecast.is_treasury_insufficient and has_escalate and has_shortfall:
            return (
                "[4] 트레저리가 충분한데 ESCALATE 를 제안했다. 채울 수 있는 "
                "부족분을 방치하는 계획이다 — 전체 거부."
            )
        return None

    @staticmethod
    def _resolve_execute_after(
        action: ProposedAction, now: datetime, notes: list[str]
    ) -> datetime | None:
        """``execute_after`` 를 정한다. 폐기해야 하면 ``None``.

        과거 ``scheduled_for`` 처리 규칙 — **승격**이 기본이다.
        --------------------------------------------------------
        예측은 매 사이클 실시간 잔고로 다시 계산된다. 따라서 이번 사이클에
        ``funding_option`` 이 존재한다는 것은 부족분이 **지금도 남아 있다**는
        뜻이다. 그 상태에서 만기가 이미 지났다면 늦은 것이지 틀린 것이 아니고,
        폐기하면 급여가 조용히 밀린다. 그래서 NOW 로 올린다.

        다만 상한을 둔다. ``STALE_SCHEDULE_TOLERANCE`` 를 넘게 과거인 시각은
        정상적인 지연으로 설명되지 않는다 — 모델이 연도를 잘못 쓰거나 시각을
        지어낸 쪽이 훨씬 그럴듯하다. 그런 계획은 승격하지 않고 폐기한다.
        """
        if action.timing == "NOW":
            return now

        scheduled = action.scheduled_for
        if scheduled is None:
            notes.append("[8] SCHEDULED 인데 scheduled_for 가 없다 — NOW 로 처리.")
            return now

        if scheduled > now:
            return scheduled

        lateness = now - scheduled
        if lateness <= STALE_SCHEDULE_TOLERANCE:
            notes.append(
                f"[8] scheduled_for 가 {lateness} 만큼 지났다. 부족분이 아직 "
                "남아 있으므로 NOW 로 승격한다."
            )
            return now

        notes.append(
            f"[8] scheduled_for 가 {lateness} 만큼 과거다 "
            f"(허용 {STALE_SCHEDULE_TOLERANCE}). 지어낸 시각으로 보고 폐기한다."
        )
        return None


__all__ = [
    "DEDUPE_WINDOW",
    "LOW_CONFIDENCE_THRESHOLD",
    "STALE_SCHEDULE_TOLERANCE",
    "AlertHook",
    "BalanceProvider",
    "CollectingAlertHook",
    "GuardAlert",
    "GuardResult",
    "InMemoryIntentLedger",
    "IntentLedger",
    "PolicyGuard",
]
