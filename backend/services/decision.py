"""Gemini 기반 판단 엔진 (스텁).

3단계에서 google-generativeai 로 구현한다.
"""

from __future__ import annotations

from typing import Any


def analyze(
    wallets: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """잔고·예정 지출·정책을 Gemini 에 넘겨 Decision 을 생성한다.

    Args:
        wallets: 현재 지갑 상태 목록 (Wallet 스키마 형태).
        payments: 판단 구간 내 예정 지출 목록 (ScheduledPayment 형태).
        policy: 현재 정책 (Policy 형태). 프롬프트에 가드레일로 포함된다.

    Returns:
        Decision 스키마 형태의 dict.
        risk_level / summary / reasoning / at_risk_payments / actions 를 포함하며,
        policy_check 는 호출자가 services.policy.validate() 로 채운다.

    Notes:
        어떤 지갑이 언제 얼마나 부족한지를 모델이 직접 계산하게 하지 않고,
        부족분은 코드로 계산해 프롬프트에 제공한 뒤 모델은 우선순위·근거·
        액션 선택만 담당하게 한다.
    """
    raise NotImplementedError("3단계에서 구현")


def build_prompt(
    wallets: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    policy: dict[str, Any],
) -> str:
    """analyze() 가 사용할 Gemini 프롬프트를 조립한다."""
    raise NotImplementedError("3단계에서 구현")


def parse_response(raw: str) -> dict[str, Any]:
    """Gemini 의 JSON 응답을 Decision dict 로 파싱한다.

    Raises:
        ValueError: 응답이 스키마를 만족하지 않는 경우.
    """
    raise NotImplementedError("3단계에서 구현")
