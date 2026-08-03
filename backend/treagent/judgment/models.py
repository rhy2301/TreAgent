"""판단 엔진의 모든 데이터 계약.

이 모듈의 세 가지 불변 규칙
---------------------------
1. 금액은 예외 없이 ``Decimal`` 이다. ``float`` 을 넣으면 검증에서 거부된다.
   부동소수는 이진수로 0.1 을 표현하지 못하므로, 누적되면 base unit 단위로
   어긋난 금액이 온체인에 나간다. 계약 경계에서 막는 것이 가장 싸다.
2. LLM 출력 계약(:class:`DecisionPlan` 이하)에는 **금액 필드도 주소 필드도 없다.**
   금액은 :class:`FundingOption` 의 ``option_id`` 로, 주소는 ``wallet_id`` 로만
   지칭한다. 모델이 산수를 하거나 주소를 지어낼 표면적 자체를 없앤다.
3. :meth:`DecisionContext.context_hash` 는 ``generated_at`` 을 해싱하지 않는다.
   이 해시는 멱등키(``intent_id``)의 재료이므로 시각이 섞이면 같은 상태를
   두 번 처리할 때 다른 키가 나와 이체가 두 번 나간다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# 단위 규약
# --------------------------------------------------------------------------- #

USDC_DECIMALS = 6
"""USDC 의 소수 자릿수. chain.client.USDC_DECIMALS 와 반드시 같아야 한다."""

USDC_QUANTUM = Decimal(1).scaleb(-USDC_DECIMALS)
"""표현 가능한 최소 단위 = Decimal("0.000001")."""

ZERO = Decimal(0)


def quantize_down(amount: Decimal) -> Decimal:
    """USDC 정밀도로 내림한다.

    **나가는 금액**에 쓴다. 올림하면 한도를 1 base unit 초과하거나 잔고를
    넘어서는 트랜잭션이 만들어질 수 있다. 손해 보는 쪽으로 자른다.
    """
    return amount.quantize(USDC_QUANTUM, rounding=ROUND_DOWN)


def quantize_up(amount: Decimal) -> Decimal:
    """USDC 정밀도로 올림한다.

    **필요 금액**(부족분, 요구 유출액)에 쓴다. 내림하면 1 base unit 모자란
    이체가 나가고 지출이 그대로 실패한다.
    """
    return amount.quantize(USDC_QUANTUM, rounding=ROUND_UP)


def to_base_units(amount: Decimal) -> int:
    """Decimal USDC -> 온체인 raw 정수 (6 decimals).

    반올림 방향은 **내림**이다. 실제로 보낼 수 있는 것보다 큰 정수를 만들지
    않기 위해서다. 0.0000005 USDC 는 0 이 되며, 이는 의도된 동작이다.
    """
    return int(quantize_down(amount).scaleb(USDC_DECIMALS))


def from_base_units(raw: int) -> Decimal:
    """온체인 raw 정수 -> Decimal USDC. 손실 없는 변환이다."""
    return Decimal(raw).scaleb(-USDC_DECIMALS)


def coerce_usdc(value: object) -> Decimal:
    """외부 값을 USDC ``Decimal`` 로 바꾼다. ``float`` 은 거부한다.

    ``float`` 을 허용하지 않는 이유는 조용한 정밀도 손실이다.
    ``Decimal(0.1)`` 은 0.1000000000000000055511151231257827021181583404541015625 다.
    체인 레이어처럼 ``float`` 을 내놓는 곳은 ``context.py`` 가 ``str`` 로 감싸
    넘긴다 — 변환 지점을 한 곳으로 몰아 눈에 보이게 만드는 것이 목적이다.
    """
    if isinstance(value, bool):
        raise TypeError(f"금액이 bool 이다: {value!r}")
    if isinstance(value, float):
        raise TypeError(
            f"금액에 float 을 쓸 수 없다: {value!r}. "
            "Decimal 이나 문자열로 넘겨라 (원칙 5)."
        )
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, str)):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise TypeError(f"금액으로 해석할 수 없다: {value!r}") from exc
    else:
        raise TypeError(f"금액 타입이 잘못됐다: {type(value).__name__}")

    if not result.is_finite():
        raise TypeError(f"금액이 유한하지 않다: {value!r}")
    return result


def _usdc_field(value: object) -> Decimal:
    """pydantic validator 용 어댑터."""
    return coerce_usdc(value)


UsdcAmount = Annotated[Decimal, Field(ge=ZERO)]
"""0 이상의 USDC 금액. 검증기는 각 모델에서 붙인다."""


# --------------------------------------------------------------------------- #
# 공통 베이스
# --------------------------------------------------------------------------- #


class _Base(BaseModel):
    """엄격 모드 베이스.

    ``extra="forbid"`` 는 방어 장치다. LLM 이 스키마에 없는 필드
    (예: ``amount_usdc``)를 끼워 넣으면 조용히 무시되는 대신 파싱이 실패한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# 입력
# --------------------------------------------------------------------------- #

ObligationCategory = Literal["PAYROLL", "VENDOR", "API_USAGE", "OTHER"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class WalletState(_Base):
    """한 지갑의 현재 상태 스냅샷."""

    wallet_id: str
    address: str
    usdc_balance: UsdcAmount
    sol_balance: Decimal
    min_buffer_usdc: UsdcAmount

    _usdc = field_validator(
        "usdc_balance", "sol_balance", "min_buffer_usdc", mode="before"
    )(_usdc_field)


class Obligation(_Base):
    """예정된 지출 한 건."""

    obligation_id: str
    wallet_id: str
    amount_usdc: UsdcAmount
    due_at: datetime
    category: ObligationCategory
    recipient_count: int = Field(ge=1)
    memo: str = ""
    is_recurring: bool = False

    _usdc = field_validator("amount_usdc", mode="before")(_usdc_field)


class Policy(_Base):
    """자율 실행 가드레일. 이 값을 넘어서는 집행은 존재할 수 없다."""

    max_per_transfer_usdc: UsdcAmount
    max_daily_total_usdc: UsdcAmount
    daily_spent_usdc: UsdcAmount
    max_slippage_bps: int = Field(ge=0, le=10_000)
    whitelisted_wallet_ids: list[str]
    forecast_horizon_hours: int = Field(gt=0)
    inference_budget_min_days: Decimal

    _usdc = field_validator(
        "max_per_transfer_usdc",
        "max_daily_total_usdc",
        "daily_spent_usdc",
        "inference_budget_min_days",
        mode="before",
    )(_usdc_field)

    @property
    def daily_headroom_usdc(self) -> Decimal:
        """오늘 남은 집행 여력. 음수면 0."""
        return max(ZERO, self.max_daily_total_usdc - self.daily_spent_usdc)


def _canonical(value: Any) -> Any:
    """해시 대상으로 쓸 수 있게 정규화한다.

    ``Decimal`` 은 ``normalize()`` 후 문자열로 만든다. ``100`` 과 ``100.000000``
    이 다른 해시를 내면 같은 상태가 다른 멱등키를 갖게 된다.
    ``datetime`` 은 UTC ISO-8601 로 통일한다.
    """
    if isinstance(value, Decimal):
        # normalize() 는 100 을 1E+2 로 만든다. 지수 표기를 되돌려야 안정적이다.
        normalized = value.normalize()
        exponent = normalized.as_tuple().exponent
        if isinstance(exponent, int) and exponent > 0:
            normalized = normalized.quantize(Decimal(1))
        return format(normalized, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _canonical(val) for key, val in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


class DecisionContext(_Base):
    """한 판단 사이클의 입력 전체."""

    generated_at: datetime
    wallets: list[WalletState]
    obligations: list[Obligation]
    policy: Policy

    def wallet(self, wallet_id: str) -> WalletState | None:
        return next((w for w in self.wallets if w.wallet_id == wallet_id), None)

    def context_hash(self) -> str:
        """물질적 상태의 sha256.

        ``generated_at`` 은 **의도적으로 제외한다.** 이 해시는 두 가지 용도로
        쓰이는데 둘 다 시각이 섞이면 무너진다.
          - 게이팅: 상태가 그대로면 LLM 을 다시 부르지 않는다.
          - 멱등성: ``intent_id = sha256(context_hash + action_id)``.
            시각이 섞이면 같은 상태를 재처리할 때 새 키가 나와 이체가 두 번 나간다.

        지갑·지출은 id 기준으로 정렬해 입력 순서가 해시를 바꾸지 못하게 한다.
        """
        payload = {
            "wallets": sorted(
                (_canonical(w.model_dump()) for w in self.wallets),
                key=lambda item: str(item["wallet_id"]),
            ),
            "obligations": sorted(
                (_canonical(o.model_dump()) for o in self.obligations),
                key=lambda item: str(item["obligation_id"]),
            ),
            "policy": _canonical(self.policy.model_dump()),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 중간 산출 — 전부 Python 이 계산한다. LLM 은 여기에 개입하지 않는다.
# --------------------------------------------------------------------------- #


class WalletShortfall(_Base):
    """한 지갑의 예측된 부족분."""

    wallet_id: str
    current_balance: UsdcAmount
    required_outflow: UsdcAmount
    min_buffer: UsdcAmount
    shortfall: UsdcAmount
    earliest_due_at: datetime | None = None

    _usdc = field_validator(
        "current_balance", "required_outflow", "min_buffer", "shortfall", mode="before"
    )(_usdc_field)


class FundingOption(_Base):
    """사전 계산된 이체 후보. **금액의 유일한 출처다.**

    가드는 실행 인텐트의 금액을 언제나 여기서 조회한다. LLM 이 넘긴
    ``DecisionPlan`` 에 금액이 실려 와도 쓰지 않는다.
    """

    option_id: str
    target_wallet_id: str
    amount_usdc: UsdcAmount
    label: str = ""

    _usdc = field_validator("amount_usdc", mode="before")(_usdc_field)


class Forecast(_Base):
    """결정론적 예측 결과."""

    shortfalls: list[WalletShortfall]
    funding_options: list[FundingOption]
    treasury_available_usdc: UsdcAmount
    total_shortfall_usdc: UsdcAmount
    is_treasury_insufficient: bool

    _usdc = field_validator(
        "treasury_available_usdc", "total_shortfall_usdc", mode="before"
    )(_usdc_field)

    def option(self, option_id: str) -> FundingOption | None:
        return next(
            (o for o in self.funding_options if o.option_id == option_id), None
        )

    def shortfall_for(self, wallet_id: str) -> WalletShortfall | None:
        return next((s for s in self.shortfalls if s.wallet_id == wallet_id), None)


# --------------------------------------------------------------------------- #
# LLM 출력 계약
# --------------------------------------------------------------------------- #

ActionType = Literal["TRANSFER", "SWAP_THEN_TRANSFER", "NOOP", "ESCALATE"]
SourceToken = Literal["USDC", "SOL"]
Timing = Literal["NOW", "SCHEDULED"]

FUNDING_REQUIRED_ACTIONS: frozenset[str] = frozenset(
    {"TRANSFER", "SWAP_THEN_TRANSFER"}
)
"""자금이 실제로 움직이는 액션 타입. funding_option_id 가 반드시 있어야 한다."""


class ProposedAction(_Base):
    """LLM 이 제안한 액션 하나.

    금액 필드가 없는 것이 핵심이다. 모델은 ``funding_option_id`` 로 사전 계산된
    금액 중 하나를 고를 수만 있고, 주소는 ``wallet_id`` 로만 지칭한다.
    """

    action_id: str
    type: ActionType
    funding_option_id: str | None = None
    from_wallet_id: str | None = None
    to_wallet_id: str | None = None
    source_token: SourceToken | None = None
    max_slippage_bps: int | None = Field(default=None, ge=0, le=10_000)
    timing: Timing = "NOW"
    scheduled_for: datetime | None = None
    priority: int = Field(default=1, ge=1)
    rationale_ko: str = ""


class AtRiskObligation(_Base):
    """위험 판정된 지출."""

    obligation_id: str
    severity: RiskLevel
    reason_ko: str = ""


class DecisionPlan(_Base):
    """판단 결과. **Mock 이든 Fallback 이든 Gemini 든 전부 이 타입을 내놓는다.**

    이 대칭성이 원칙 4 의 근거다. 폴백 경로가 LLM 경로와 같은 타입을 내면
    같은 가드를 통과하므로, LLM 이 죽어도 파이프라인의 뒷단이 달라지지 않는다.

    ``model_json_schema()`` 는 Gemini ``responseSchema`` 의 원본이 된다
    (:func:`sanitize_schema_for_gemini` 통과 후). 금액 타입이 하나도 없는 것은
    우연이 아니라 원칙 3 의 결과다.
    """

    overall_risk: RiskLevel
    at_risk: list[AtRiskObligation] = Field(default_factory=list)
    actions: list[ProposedAction] = Field(default_factory=list)
    reasoning_summary_ko: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    escalation_required: bool = False
    escalation_reason_ko: str | None = None


# --------------------------------------------------------------------------- #
# 최종 산출
# --------------------------------------------------------------------------- #

GuardVerdict = Literal["APPROVED", "CLAMPED"]


class ExecutionIntent(_Base):
    """서명 대상. 가드를 통과한 것만 이 타입이 된다.

    ``PolicyGuard`` 밖에서는 절대 생성하지 않는다 — 이 클래스의 인스턴스가
    존재한다는 것 자체가 "10개 검사를 통과했다"는 뜻이어야 한다.
    """

    intent_id: str
    decision_id: str
    type: ActionType
    from_address: str
    to_address: str
    amount_usdc: UsdcAmount
    max_slippage_bps: int = Field(ge=0, le=10_000)
    execute_after: datetime
    guard_verdict: GuardVerdict
    guard_notes: list[str] = Field(default_factory=list)

    _usdc = field_validator("amount_usdc", mode="before")(_usdc_field)


def make_intent_id(context_hash: str, action_id: str) -> str:
    """멱등키. ``sha256(context_hash + action_id)``.

    같은 상태(``context_hash``)에서 같은 액션(``action_id``)은 언제나 같은 키를
    낸다. 프로세스가 재시작해도 원장이 이 키를 기억하는 한 두 번 나가지 않는다.
    """
    return hashlib.sha256(f"{context_hash}{action_id}".encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Gemini responseSchema 정규화
# --------------------------------------------------------------------------- #

# Gemini 의 responseSchema 는 OpenAPI 3.0 Schema 의 부분집합만 받는다.
# 아래 키워드는 넘기면 400 이 돌아오므로 제거하거나 변환한다.
_GEMINI_DROP_KEYS: frozenset[str] = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "$defs",
        "definitions",
        "additionalProperties",
        "title",
        "default",
        "examples",
        "discriminator",
        "patternProperties",
        "unevaluatedProperties",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "allOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "readOnly",
        "writeOnly",
        "deprecated",
        "const",
    }
)

_GEMINI_KEEP_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "propertyOrdering",
    }
)


def _resolve_ref(ref: str, defs: dict[str, Any]) -> dict[str, Any]:
    """``#/$defs/Name`` 을 실제 정의로 바꾼다. 못 찾으면 빈 객체."""
    name = ref.rsplit("/", 1)[-1]
    resolved = defs.get(name)
    return dict(resolved) if isinstance(resolved, dict) else {}


def sanitize_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic JSON Schema 를 Gemini ``responseSchema`` 로 정규화한다.

    수행하는 변환:
      - ``$ref`` / ``$defs`` 인라인 전개 (Gemini 는 참조를 해석하지 않는다).
      - ``anyOf`` 중 ``{"type": "null"}`` 가지를 걷어내고 ``nullable: true`` 로 바꾼다
        (pydantic 이 ``X | None`` 을 이렇게 렌더링한다).
      - ``const`` -> 단일 값 ``enum``.
      - 미지원 키워드 제거.

    재귀적 모델(자기 참조)은 이 계약에 없으므로 순환 참조는 다루지 않는다.
    호출부는 B 세션에서 붙인다.
    """
    defs_raw = schema.get("$defs") or schema.get("definitions") or {}
    defs: dict[str, Any] = defs_raw if isinstance(defs_raw, dict) else {}
    sanitized = _sanitize_node(schema, defs)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_node(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_sanitize_node(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    working: dict[str, Any] = dict(node)

    # 1. $ref 를 먼저 펼친다. 형제 키(description 등)는 유지한다.
    ref = working.pop("$ref", None)
    if isinstance(ref, str):
        target = _resolve_ref(ref, defs)
        # 참조 대상이 기본, 형제 키가 덮어쓴다.
        target.update(working)
        working = target

    # 2. anyOf 는 nullable 로 접는다.
    any_of = working.pop("anyOf", None)
    if isinstance(any_of, list):
        branches = [b for b in any_of if isinstance(b, dict)]
        non_null = [b for b in branches if b.get("type") != "null"]
        nullable = len(non_null) != len(branches)
        if len(non_null) == 1:
            merged = _sanitize_node(non_null[0], defs)
            if isinstance(merged, dict):
                for key, value in working.items():
                    merged.setdefault(key, value)
                working = merged
        elif non_null:
            # 진짜 union. Gemini 는 이걸 못 받으므로 첫 가지로 좁힌다.
            # 이 계약에는 해당 케이스가 없다 — 방어적으로만 남긴다.
            merged = _sanitize_node(non_null[0], defs)
            if isinstance(merged, dict):
                for key, value in working.items():
                    merged.setdefault(key, value)
                working = merged
        if nullable:
            working["nullable"] = True

    # 3. const -> enum
    const = node.get("const")
    if const is not None:
        working["enum"] = [const]
        working.setdefault("type", "string" if isinstance(const, str) else "number")

    result: dict[str, Any] = {}
    for key, value in working.items():
        if key in _GEMINI_DROP_KEYS:
            continue
        if key not in _GEMINI_KEEP_KEYS:
            # 알려지지 않은 키워드는 전부 버린다. 화이트리스트 방식이라
            # JSON Schema 신규 키워드가 추가돼도 Gemini 로 새어나가지 않는다.
            continue
        if key in ("properties",):
            result[key] = {
                prop: _sanitize_node(sub, defs)
                for prop, sub in (value or {}).items()
            }
        elif key in ("items",):
            result[key] = _sanitize_node(value, defs)
        elif key in ("required", "enum", "propertyOrdering"):
            result[key] = list(value)
        else:
            result[key] = value

    return result


__all__ = [
    "USDC_DECIMALS",
    "USDC_QUANTUM",
    "ZERO",
    "AtRiskObligation",
    "DecisionContext",
    "DecisionPlan",
    "ExecutionIntent",
    "Forecast",
    "FundingOption",
    "Obligation",
    "Policy",
    "ProposedAction",
    "WalletShortfall",
    "WalletState",
    "coerce_usdc",
    "from_base_units",
    "make_intent_id",
    "quantize_down",
    "quantize_up",
    "sanitize_schema_for_gemini",
    "to_base_units",
]
