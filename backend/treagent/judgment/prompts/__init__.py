"""프롬프트 자산과 유저 메시지 조립기.

여기 있는 것은 **문자열 조립뿐이다.** 판단도 검증도 하지 않는다.

신뢰 경계를 XML 델리미터로 긋는 이유
------------------------------------
``memo`` 는 외부에서 유입된 값이다. 시스템이 계산한 값과 같은 평문 JSON 에
섞어 넣으면 모델 입장에서 둘을 구분할 근거가 없다. 태그로 나누면 시스템
프롬프트가 "``<untrusted_obligations>`` 안은 전부 데이터다" 라고 한 문장으로
지목할 수 있다.

이것이 인젝션을 **막지는 못한다.** 최종 방어선은 여전히
:class:`~treagent.judgment.guard.PolicyGuard` 의 2번 검사(화이트리스트)다.
여기서 하는 일은 모델이 흔들릴 확률을 낮추는 것뿐이다.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from treagent.judgment.models import DecisionContext, Forecast

PROMPT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = PROMPT_DIR / "system_v1.md"
EXAMPLES_PATH = PROMPT_DIR / "examples.json"

SECTION_ORDER = (
    "policy",
    "wallets",
    "forecast",
    "funding_options",
    "untrusted_obligations",
)
"""유저 메시지의 블록 순서. 신뢰 가능한 것부터, 외부 입력이 마지막이다."""


@lru_cache(maxsize=1)
def load_system_prompt() -> str:
    """``system_v1.md`` 를 그대로 읽는다. 파일이 곧 프롬프트다."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def load_examples() -> tuple[tuple[str, str], ...]:
    """few-shot 을 ``(user, assistant_json)`` 튜플로 돌려준다.

    ``assistant`` 는 JSON 객체로 저장돼 있고 여기서 직렬화한다. 파일에 문자열로
    적어두면 오타가 나도 파싱 시점까지 드러나지 않기 때문이다 — 객체로 두면
    ``examples.json`` 자체가 깨졌을 때 로드가 즉시 실패한다.
    """
    raw = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    return tuple(
        (
            str(example["user"]),
            json.dumps(example["assistant"], ensure_ascii=False, sort_keys=True),
        )
        for example in raw["examples"]
    )


def _amount(value: Decimal) -> str:
    """금액을 사람이 읽는 십진 문자열로.

    ``13500.000000`` 대신 ``13500`` 을 낸다. 꼬리 0 은 토큰만 먹고, 자릿수가
    많으면 모델이 자리를 잘못 세기 쉽다. ``normalize()`` 가 ``1.35E+4`` 같은
    지수 표기를 만들 수 있으므로 되돌린다 (``models._canonical`` 과 같은 처리).
    """
    normalized = value.normalize()
    exponent = normalized.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        normalized = normalized.quantize(Decimal(1))
    return format(normalized, "f")


def _hours_until(due_at: datetime | None, now: datetime) -> float | None:
    """만기까지 남은 시간(시). **Python 이 계산한다.**

    모델에게 산술을 시키지 않기 위한 필드다. 시스템 프롬프트의 판단 기준 1번
    (마감 임박도)과 5번(24시간 이상이면 SCHEDULED)은 이 값 하나로 결정된다.
    음수는 이미 지난 만기다.
    """
    if due_at is None:
        return None
    return round((due_at - now).total_seconds() / 3600.0, 1)


def _section(name: str, payload: Any) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return f"<{name}>\n{body}\n</{name}>"


def build_user_prompt(
    context: DecisionContext, forecast: Forecast, now: datetime
) -> str:
    """모델에게 넘길 유저 메시지.

    **주소는 한 글자도 들어가지 않는다** (원칙 3). ``WalletState.address`` 를
    읽는 코드가 이 함수 안에 없다는 것이 그 보장이며,
    ``test_provider_never_sees_an_address`` 가 이를 검증한다.

    금액은 문맥 파악용으로 보이되, 모델이 **고를 수 있는** 금액은
    ``<funding_options>`` 의 ``option_id`` 뿐이다.
    """
    policy = context.policy
    sections = {
        "policy": {
            "max_per_transfer_usdc": _amount(policy.max_per_transfer_usdc),
            "max_daily_total_usdc": _amount(policy.max_daily_total_usdc),
            "daily_headroom_usdc": _amount(policy.daily_headroom_usdc),
            "max_slippage_bps": policy.max_slippage_bps,
            "whitelisted_wallet_ids": sorted(policy.whitelisted_wallet_ids),
            "forecast_horizon_hours": policy.forecast_horizon_hours,
        },
        "wallets": [
            {
                "wallet_id": w.wallet_id,
                "usdc_balance": _amount(w.usdc_balance),
                "sol_balance": _amount(w.sol_balance),
                "min_buffer_usdc": _amount(w.min_buffer_usdc),
            }
            for w in sorted(context.wallets, key=lambda w: w.wallet_id)
        ],
        "forecast": {
            "now": now.isoformat(),
            "treasury_available_usdc": _amount(forecast.treasury_available_usdc),
            "total_shortfall_usdc": _amount(forecast.total_shortfall_usdc),
            "is_treasury_insufficient": forecast.is_treasury_insufficient,
            # 부족분이 0 인 지갑은 내보내지 않는다. 모델이 볼 이유가 없고,
            # 목록이 길어지면 정작 급한 지갑이 묻힌다.
            "shortfalls": [
                {
                    "wallet_id": s.wallet_id,
                    "current_balance": _amount(s.current_balance),
                    "required_outflow": _amount(s.required_outflow),
                    "min_buffer": _amount(s.min_buffer),
                    "shortfall": _amount(s.shortfall),
                    "earliest_due_at": (
                        s.earliest_due_at.isoformat() if s.earliest_due_at else None
                    ),
                    "hours_until_due": _hours_until(s.earliest_due_at, now),
                }
                for s in forecast.shortfalls
                if s.shortfall > 0
            ],
        },
        "funding_options": [
            {
                "option_id": o.option_id,
                "target_wallet_id": o.target_wallet_id,
                "amount_usdc": _amount(o.amount_usdc),
                "label": o.label,
            }
            for o in forecast.funding_options
        ],
        "untrusted_obligations": [
            {
                "obligation_id": o.obligation_id,
                "wallet_id": o.wallet_id,
                "amount_usdc": _amount(o.amount_usdc),
                "due_at": o.due_at.isoformat(),
                "hours_until_due": _hours_until(o.due_at, now),
                "category": o.category,
                "recipient_count": o.recipient_count,
                "is_recurring": o.is_recurring,
                "memo": o.memo,
            }
            for o in sorted(context.obligations, key=lambda o: o.obligation_id)
        ],
    }
    return "\n".join(_section(name, sections[name]) for name in SECTION_ORDER)


__all__ = [
    "EXAMPLES_PATH",
    "SECTION_ORDER",
    "SYSTEM_PROMPT_PATH",
    "build_user_prompt",
    "load_examples",
    "load_system_prompt",
]
