"""정책 검증 엔진 (스텁).

현재 로직은 mock_data.validate_action() 에 임시로 들어가 있고,
3단계에서 이 모듈로 옮긴다.
"""

from __future__ import annotations

from typing import Any


def validate(action: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """단일 액션이 정책을 만족하는지 검사한다.

    검사 항목:
        1. 1회 이체 한도 (max_single_transfer)
        2. 일일 누적 한도 (daily_used + amount <= max_daily_total)
        3. 수신 주소 화이트리스트 등록 여부
        4. 출금 지갑의 최소 버퍼 침해 여부 (min_buffers)
        5. 스왑인 경우 슬리피지 한도 (max_slippage_bps)

    Args:
        action: ProposedAction 형태의 dict.
        policy: Policy 형태의 dict.

    Returns:
        PolicyCheck 형태의 dict — {"passed", "violations", "requires_approval"}.
    """
    raise NotImplementedError("3단계에서 구현")


def validate_batch(
    actions: list[dict[str, Any]], policy: dict[str, Any]
) -> dict[str, Any]:
    """액션 묶음 전체를 검증한다. 누적 금액이 일일 한도를 넘는지도 함께 본다."""
    raise NotImplementedError("3단계에서 구현")


def is_whitelisted(address: str, policy: dict[str, Any]) -> bool:
    """주소가 화이트리스트에 있는지 확인한다."""
    raise NotImplementedError("3단계에서 구현")
