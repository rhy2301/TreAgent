"""지갑 조회 라우터."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import mock_data
from schemas import Wallet, WalletDetail

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


@router.get("", response_model=list[Wallet], summary="전체 지갑 목록")
def list_wallets() -> list[Wallet]:
    """트레저리가 관리하는 Solana devnet 지갑 4개를 반환한다."""
    return [Wallet(**wallet) for wallet in mock_data.list_wallets()]


@router.get("/{wallet_id}", response_model=WalletDetail, summary="지갑 상세")
def get_wallet(wallet_id: str) -> WalletDetail:
    """지갑 상세와 최근 트랜잭션을 반환한다. 없으면 404."""
    wallet = mock_data.get_wallet(wallet_id)
    if wallet is None:
        raise HTTPException(status_code=404, detail=f"wallet '{wallet_id}' not found")

    return WalletDetail(
        **wallet,
        recent_transactions=mock_data.wallet_transactions(wallet_id),
    )
