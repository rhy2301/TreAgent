"""Jupiter 스왑 (스텁).

4단계에서 구현한다. USDC 보유고가 임계치 아래로 내려갔을 때 vault 의 SOL 일부를
USDC 로 바꿔 예정 지출을 커버하는 경로다.

구현 시 주의
------------
- Jupiter Quote API(v6) 로 견적을 받고, 정책의 max_slippage_bps 안에 드는지
  먼저 확인한 뒤에만 swap 트랜잭션을 요청한다.
- Jupiter 가 돌려주는 것은 이미 직렬화된 VersionedTransaction 이다.
  blockhash 를 갈아끼우고 서명만 얹어 보내면 된다.
- 견적에는 유효시간이 있다. 만료된 견적으로 제출하면 슬리피지 초과로 실패한다.
- 반환 금액 단위는 이 모듈 경계에서 float 로 바꿔 내보낸다 (chain.client.to_ui).
"""

from __future__ import annotations

from typing import Any


async def get_quote(
    input_token: str, output_token: str, amount: float
) -> dict[str, Any]:
    """Jupiter 에서 스왑 견적을 받는다.

    Args:
        input_token: 입력 토큰 심볼 (예: "SOL").
        output_token: 출력 토큰 심볼 (예: "USDC").
        amount: 입력 금액 (UI 단위).

    Returns:
        schemas.SwapQuote 형태의 dict.
    """
    raise NotImplementedError("4단계에서 구현")


async def swap(
    from_wallet_id: str,
    input_token: str,
    output_token: str,
    amount: float,
    max_slippage_bps: int = 50,
    dry_run: bool = False,
) -> dict[str, Any]:
    """견적을 받아 스왑을 실행하고 TxRecord 형태의 dict 를 반환한다.

    Raises:
        NotImplementedError: 아직 미구현.
    """
    raise NotImplementedError("4단계에서 구현")
