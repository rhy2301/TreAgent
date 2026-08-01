"""실행 오케스트레이터 (스텁).

정책 검증 → USDC 이체 또는 Jupiter 스왑 후 이체 → Solana Pay(x402) 결제 →
온체인 로그 기록까지의 흐름을 담당한다. 3단계에서 구현한다.
"""

from __future__ import annotations

from typing import Any, Optional


def execute(
    decision: dict[str, Any], action_indices: Optional[list[int]] = None
) -> dict[str, Any]:
    """Decision 의 액션을 순서대로 온체인에 실행한다.

    Args:
        decision: Decision 형태의 dict.
        action_indices: 실행할 액션 인덱스. None 이면 전체 실행.

    Returns:
        ExecutionResult 형태의 dict.
        정책 위반이면 예외 대신 status="blocked" 와 blocked_reason 을 담아 반환한다.

    Notes:
        실행 직전에 services.policy.validate() 로 반드시 재검증한다.
        판단 시점과 실행 시점 사이에 잔고나 정책이 바뀌었을 수 있기 때문이다.
    """
    raise NotImplementedError("3단계에서 구현")


def execute_action(action: dict[str, Any]) -> dict[str, Any]:
    """단일 액션을 실행하고 TxRecord 형태의 dict 를 반환한다."""
    raise NotImplementedError("3단계에서 구현")


def swap_and_transfer(
    from_wallet: str, to_wallet: str, amount: float, input_token: str
) -> list[dict[str, Any]]:
    """Jupiter 로 스왑한 뒤 목적지 지갑으로 이체한다. TxRecord 2건을 반환한다."""
    raise NotImplementedError("3단계에서 구현")


def pay_batch(payment: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    """Solana Pay(x402) 로 수신자 전원에게 배치 송금한다.

    Returns:
        PayResult 형태의 dict.
    """
    raise NotImplementedError("3단계에서 구현")
