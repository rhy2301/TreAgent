"""Solana devnet RPC 연동 (스텁).

3단계에서 solders / solana-py 로 구현한다.
"""

from __future__ import annotations

from typing import Any, Optional


def get_balances(address: str) -> dict[str, float]:
    """주소의 SOL / USDC 잔고를 조회한다.

    Args:
        address: Solana devnet 주소 (base58).

    Returns:
        {"sol": float, "usdc": float} 형태의 잔고.
        USDC 는 devnet SPL 민트의 Associated Token Account 를 조회한다.
    """
    raise NotImplementedError("3단계에서 구현")


def transfer_usdc(from_address: str, to_address: str, amount: float) -> str:
    """USDC SPL 토큰을 이체하고 트랜잭션 서명을 반환한다.

    Args:
        from_address: 출금 지갑 주소. 개인키는 환경변수에서 로드한다.
        to_address: 수신 지갑 주소.
        amount: 이체할 USDC 금액 (UI 단위, 내부에서 6 decimals 로 변환).

    Returns:
        확정된 트랜잭션의 base58 서명.

    Raises:
        InsufficientFundsError: 잔고 또는 가스비용 SOL 이 부족한 경우.
    """
    raise NotImplementedError("3단계에서 구현")


def get_transaction(signature: str) -> Optional[dict[str, Any]]:
    """서명으로 확정된 트랜잭션 상세를 조회한다."""
    raise NotImplementedError("3단계에서 구현")


def request_airdrop(address: str, sol_amount: float = 1.0) -> str:
    """devnet faucet 에서 SOL 을 받아 가스비를 확보한다."""
    raise NotImplementedError("3단계에서 구현")
