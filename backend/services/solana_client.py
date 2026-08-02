"""Solana devnet 연동의 서비스 레이어 접합부.

라우터와 온체인 레이어(chain/) 사이의 유일한 통로다.

MOCK_MODE
---------
settings.MOCK_MODE 가 true 면 mock_data 를, false 면 chain.transfer 의 실제
온체인 호출을 쓴다. 시연 도중 devnet RPC 가 죽거나 레이트리밋에 걸렸을 때
환경변수 하나로 되돌아갈 수 있도록 남겨둔 폴백이다.

두 경로의 반환 dict 는 키와 타입이 정확히 같아야 한다. 한쪽에 키를 추가하면
반드시 다른 쪽에도 추가할 것. 라우터는 어느 쪽이 응답했는지 알 수 없어야 한다.

chain.* 은 함수 안에서 import 한다. solana-py 나 .env 가 깨진 상황에서도
MOCK_MODE 로 서버가 뜨는 것이 이 폴백의 존재 이유이기 때문이다.
"""

from __future__ import annotations

from typing import Any, Optional

import mock_data
from config import settings

# 금액은 이 레이어 위아래 모두 float USDC 단위다. raw 정수는 chain/ 안에만 있다.


def _mock() -> bool:
    """호출 시점의 MOCK_MODE. 모듈 로드 시점에 고정하지 않는다."""
    return settings.MOCK_MODE


def _wallet_id_for(address: str) -> Optional[str]:
    """주소 -> 지갑 id. 외부 주소면 None."""
    wallet = mock_data.get_wallet_by_address(address)
    return wallet["id"] if wallet else None


# --------------------------------------------------------------------------- #
# 잔고
# --------------------------------------------------------------------------- #


async def get_balances(address: str) -> dict[str, float]:
    """주소의 USDC / SOL 잔고를 조회한다.

    Returns:
        {"usdc": float, "sol": float}. USDC 는 devnet SPL 민트의 ATA 를 읽으며,
        ATA 가 아직 없으면 0.0 이다.
    """
    if _mock():
        wallet = mock_data.get_wallet_by_address(address)
        if wallet is None:
            return {"usdc": 0.0, "sol": 0.0}
        return {"usdc": wallet["usdc_balance"], "sol": wallet["sol_balance"]}

    from chain import transfer

    return await transfer.get_balances(address)


async def get_all_balances() -> dict[str, dict[str, float]]:
    """트레저리 지갑 4개의 잔고를 한 번에 조회한다.

    Returns:
        {wallet_id: {"usdc": float, "sol": float}}
    """
    if _mock():
        return {
            wallet["id"]: {"usdc": wallet["usdc_balance"], "sol": wallet["sol_balance"]}
            for wallet in mock_data.list_wallets()
        }

    from chain import transfer

    return await transfer.get_all_balances()


# --------------------------------------------------------------------------- #
# 전송
# --------------------------------------------------------------------------- #


async def transfer_usdc(
    from_wallet: str,
    to_address: str,
    amount: float,
    dry_run: bool = False,
) -> dict[str, Any]:
    """USDC SPL 토큰을 이체한다.

    Args:
        from_wallet: 출금 지갑 id (vault / operations / payroll / vendor).
            개인키는 chain.wallet 이 환경변수에서 로드한다.
        to_address: 수신 지갑 주소 (base58).
        amount: 이체할 USDC 금액 (float, 소수점 6자리까지).
        dry_run: True 면 시뮬레이션만 하고 서명·제출하지 않는다.

    Returns:
        signature / explorer_url / amount / from / to / dry_run / confirmed_at.
        dry_run 이면 signature, explorer_url, confirmed_at 이 None 이다.

    Raises:
        chain.exceptions.SolanaError: 잔고 부족, 주소 오류, 확정 실패 등.
            라우터에서 잡지 않고 main.py 의 exception handler 로 흘려보낸다.
    """
    if _mock():
        return _mock_transfer(from_wallet, to_address, amount, dry_run)

    from chain import transfer

    return await transfer.transfer_usdc(from_wallet, to_address, amount, dry_run=dry_run)


def _mock_transfer(
    from_wallet: str, to_address: str, amount: float, dry_run: bool
) -> dict[str, Any]:
    """실제 전송과 같은 모양의 결과를 만들고 mock 잔고를 움직인다."""
    from chain.exceptions import InsufficientBalance, InvalidAddress, InvalidAmount

    if amount <= 0:
        raise InvalidAmount(f"금액은 0보다 커야 한다: {amount}", amount=amount)

    source = mock_data.get_wallet(from_wallet)
    if source is None:
        raise InvalidAddress(f"알 수 없는 출금 지갑 id: {from_wallet}")
    if source["usdc_balance"] < amount:
        raise InsufficientBalance(
            required=amount,
            available=source["usdc_balance"],
            address=source["address"],
        )

    if dry_run:
        return {
            "signature": None,
            "explorer_url": None,
            "amount": amount,
            "from": source["address"],
            "to": to_address,
            "dry_run": True,
            "confirmed_at": None,
        }

    target_id = _wallet_id_for(to_address)
    mock_data.adjust_balance(from_wallet, -amount)
    if target_id:
        mock_data.adjust_balance(target_id, amount)

    tx = mock_data.make_tx("transfer", amount, from_wallet=from_wallet, to_wallet=target_id)
    mock_data.add_transaction(tx)

    return {
        "signature": tx["signature"],
        "explorer_url": tx["explorer_url"],
        "amount": amount,
        "from": source["address"],
        "to": to_address,
        "dry_run": False,
        "confirmed_at": tx["confirmed_at"],
    }


# --------------------------------------------------------------------------- #
# 조회
# --------------------------------------------------------------------------- #


async def get_recent_transactions(address: str, limit: int = 10) -> list[dict[str, Any]]:
    """주소의 최근 트랜잭션을 최신순으로 반환한다.

    Returns:
        schemas.TxRecord 형태의 dict 리스트.
    """
    if _mock():
        wallet_id = _wallet_id_for(address)
        if wallet_id is None:
            return []
        return mock_data.wallet_transactions(wallet_id, limit)

    from chain import transfer

    return await transfer.get_recent_transactions(address, limit)


async def get_transaction(signature: str) -> Optional[dict[str, Any]]:
    """서명으로 확정된 트랜잭션 상세를 조회한다. 없으면 None."""
    if _mock():
        return next(
            (tx for tx in mock_data.TRANSACTIONS if tx["signature"] == signature), None
        )

    from chain import transfer

    return await transfer.get_transaction(signature)


async def request_airdrop(address: str, sol_amount: float = 1.0) -> str:
    """devnet faucet 에서 SOL 을 받아 가스비를 확보한다."""
    if _mock():
        return mock_data.make_signature()

    from chain import transfer

    return await transfer.request_airdrop(address, sol_amount)
