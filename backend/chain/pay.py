"""Solana Pay(x402) 배치 결제 (스텁).

4단계에서 구현한다. 급여 배치처럼 수신자가 여러 명인 지출을 한 번에 처리한다.

구현 시 주의
------------
- 수신자 전원을 한 트랜잭션에 넣을 수 없다. 트랜잭션 크기 한도(1232바이트) 때문에
  transfer_checked 는 대략 6~8건, ATA 생성이 끼면 그보다 적게 들어간다.
  묶음으로 나누고 묶음별 결과를 RecipientResult 로 모은다.
- 부분 성공이 정상 시나리오다. 실패한 수신자만 재시도할 수 있어야 하므로
  성공한 서명을 반드시 기록하고 전체 롤백은 시도하지 않는다.
- 각 수신자 주소는 정책 화이트리스트를 통과했는지 실행 직전에 재확인한다.
- 금액 합이 payment.amount 와 일치하는지 대조한 뒤에 보낸다.
"""

from __future__ import annotations

from typing import Any


async def pay_batch(payment: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    """수신자 전원에게 USDC 를 배치 송금한다.

    Args:
        payment: schemas.ScheduledPayment 형태의 dict. recipients 를 사용한다.
        dry_run: True 면 시뮬레이션까지만 한다.

    Returns:
        schemas.PayResult 형태의 dict.
    """
    raise NotImplementedError("4단계에서 구현")


async def pay_one(
    from_wallet_id: str, recipient: dict[str, Any], dry_run: bool = False
) -> dict[str, Any]:
    """수신자 한 명에게 결제하고 RecipientResult 형태의 dict 를 반환한다."""
    raise NotImplementedError("4단계에서 구현")
