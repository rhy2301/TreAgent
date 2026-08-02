"""RPC 클라이언트 싱글턴, 네트워크 상수, 단위 변환.

단위 변환 규칙 (자금 사고 방지의 핵심)
--------------------------------------
온체인 raw 정수(6 decimals)는 이 패키지 안에서만 존재한다.
API·서비스 레이어는 예외 없이 float USDC 단위를 주고받으며,
경계를 넘는 순간 to_raw / to_ui 를 통과한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey

from chain.exceptions import ConfigError, InvalidAddress

# --------------------------------------------------------------------------- #
# .env 로딩
# --------------------------------------------------------------------------- #

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent

# 레포 루트의 .env 를 먼저 읽고, backend/.env 가 있으면 덮어쓴다.
for _candidate in (_REPO_ROOT / ".env", _BACKEND_DIR / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=True)


def _env(name: str, default: str = "") -> str:
    """환경변수를 읽고 앞뒤 공백과 따옴표를 정리한다."""
    return os.getenv(name, default).strip().strip("'\"")


# --------------------------------------------------------------------------- #
# 네트워크 상수
# --------------------------------------------------------------------------- #

RPC_URL: str = _env("SOLANA_RPC_URL") or "https://api.devnet.solana.com"
NETWORK: str = _env("SOLANA_NETWORK") or "devnet"
USDC_DECIMALS: int = 6

# 자체 발행한 devnet SPL 토큰의 mint 주소. 없으면 잔고/전송 시점에 ConfigError.
USDC_MINT_STR: str = _env("USDC_MINT")

LAMPORTS_PER_SOL: int = 1_000_000_000

# 수수료(5,000 lamports 수준) + 수신자 ATA rent(~0.00204 SOL) 를 덮는 하한.
MIN_SOL_FOR_FEES: float = 0.01

# RPC 호출 타임아웃 (초). 공용 devnet 엔드포인트가 느릴 때를 감안한다.
RPC_TIMEOUT: float = 30.0

# 트랜잭션 확정 대기 상한 (초).
CONFIRM_TIMEOUT: float = 30.0

# confirm/blockhash 만료 시 재시도 횟수 (최초 시도 제외).
MAX_SEND_RETRIES: int = 2

DEFAULT_COMMITMENT = Confirmed

_EXPLORER_TX = "https://explorer.solana.com/tx/{signature}?cluster={cluster}"
_EXPLORER_ADDRESS = "https://explorer.solana.com/address/{address}?cluster={cluster}"


# --------------------------------------------------------------------------- #
# 클라이언트 싱글턴
# --------------------------------------------------------------------------- #

_client: Optional[AsyncClient] = None


def get_client() -> AsyncClient:
    """프로세스 전역 AsyncClient 를 반환한다.

    FastAPI lifespan 에서 close_client() 로 정리한다. 동기 Client 는 async 라우터를
    블로킹하므로 이 패키지 어디에서도 쓰지 않는다.
    """
    global _client
    if _client is None:
        _client = AsyncClient(
            endpoint=RPC_URL,
            commitment=DEFAULT_COMMITMENT,
            timeout=RPC_TIMEOUT,
        )
    return _client


async def close_client() -> None:
    """열려 있는 HTTP 연결을 닫는다. 재호출해도 안전하다."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


# --------------------------------------------------------------------------- #
# 단위 변환
# --------------------------------------------------------------------------- #


def to_raw(amount: float) -> int:
    """float USDC -> 온체인 raw 정수.

    round() 없이 int() 로 자르면 부동소수 오차 때문에 금액이 1 유닛씩 깎인다.
    (예: 2.01 * 10**6 == 2009999.9999999998 이라 int() 는 2009999 를 준다)
    """
    return int(round(amount * (10**USDC_DECIMALS)))


def to_ui(raw: int) -> float:
    """온체인 raw 정수 -> float USDC."""
    return round(raw / (10**USDC_DECIMALS), USDC_DECIMALS)


def lamports_to_sol(lamports: int) -> float:
    return round(lamports / LAMPORTS_PER_SOL, 9)


def sol_to_lamports(sol: float) -> int:
    return int(round(sol * LAMPORTS_PER_SOL))


def has_excess_precision(amount: float) -> bool:
    """USDC 소수점 6자리를 넘는 금액인지 판정한다."""
    return abs(amount * (10**USDC_DECIMALS) - round(amount * (10**USDC_DECIMALS))) > 1e-6


# --------------------------------------------------------------------------- #
# 주소 / URL 헬퍼
# --------------------------------------------------------------------------- #


def parse_pubkey(address: str, label: str = "주소") -> Pubkey:
    """base58 문자열을 Pubkey 로 파싱한다. 실패하면 InvalidAddress."""
    if not address or not isinstance(address, str):
        raise InvalidAddress(f"{label}가 비어 있다.", address=str(address))
    try:
        return Pubkey.from_string(address.strip())
    except (ValueError, TypeError) as exc:
        raise InvalidAddress(
            f"{label} 파싱 실패: '{address}' 는 유효한 Solana base58 주소가 아니다.",
            address=address,
        ) from exc


def get_usdc_mint() -> Pubkey:
    """USDC mint Pubkey. 환경변수가 비어 있으면 ConfigError."""
    if not USDC_MINT_STR:
        raise ConfigError(
            "USDC_MINT 환경변수가 비어 있다. devnet 에서 자체 발행한 SPL 토큰의 "
            "mint 주소를 .env 에 설정해야 한다."
        )
    return parse_pubkey(USDC_MINT_STR, "USDC_MINT")


def explorer_url(signature: str) -> str:
    """트랜잭션 서명의 Solana Explorer URL."""
    return _EXPLORER_TX.format(signature=signature, cluster=NETWORK)


def explorer_address_url(address: str) -> str:
    """계정 주소의 Solana Explorer URL."""
    return _EXPLORER_ADDRESS.format(address=address, cluster=NETWORK)
