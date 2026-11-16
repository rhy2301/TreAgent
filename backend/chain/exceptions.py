"""온체인 레이어 도메인 예외.

라우터는 try/except 를 두지 않는다. 여기 정의된 예외를 그대로 올려보내면
main.py 에 등록된 exception handler 가 status_code 로 변환한다.
"""

from __future__ import annotations

from typing import Any


class SolanaError(Exception):
    """온체인 레이어에서 발생하는 모든 예외의 베이스.

    Attributes:
        status_code: main.py 핸들러가 그대로 사용하는 HTTP 상태 코드.
        code: 프런트가 분기용으로 쓰는 안정적인 문자열 식별자.
        detail: 사용자에게 보여줄 메시지.
        context: 응답 body 에 함께 실릴 부가 정보. 비밀값을 넣지 말 것.
    """

    status_code: int = 500
    code: str = "solana_error"

    def __init__(self, detail: str, **context: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context: dict[str, Any] = context

    def to_dict(self) -> dict[str, Any]:
        """JSONResponse body 로 쓸 dict."""
        return {"error": self.code, "detail": self.detail, **self.context}


class InsufficientBalance(SolanaError):
    """USDC 잔고가 요청 금액에 못 미친다."""

    status_code = 409
    code = "insufficient_balance"

    def __init__(self, required: float, available: float, address: str) -> None:
        super().__init__(
            f"USDC 잔고 부족: {required:,.6f} 필요하지만 {available:,.6f} 보유 "
            f"(부족분 {required - available:,.6f} USDC)",
            required=required,
            available=available,
            address=address,
        )


class InsufficientSol(SolanaError):
    """수수료 + ATA rent 로 쓸 SOL 이 부족하다."""

    status_code = 409
    code = "insufficient_sol"

    def __init__(self, required: float, available: float, address: str) -> None:
        super().__init__(
            f"SOL 잔고 부족: 수수료와 ATA rent 로 최소 {required} SOL 이 필요하지만 "
            f"{available} SOL 보유. devnet faucet 으로 충전이 필요하다.",
            required=required,
            available=available,
            address=address,
        )


class InvalidAddress(SolanaError):
    """base58 주소 파싱 실패 또는 금액 형식 오류."""

    status_code = 422
    code = "invalid_address"

    def __init__(self, detail: str, **context: Any) -> None:
        super().__init__(detail, **context)


class InvalidAmount(InvalidAddress):
    """금액이 0 이하이거나 USDC 소수점 6자리를 넘었다.

    검증 실패라는 성격이 같아 422 를 공유한다.
    """

    code = "invalid_amount"


class TransactionFailed(SolanaError):
    """트랜잭션이 제출되었으나 확정에 실패했다.

    이미 제출된 서명이 있으면 반드시 `signature` 로 넘긴다. 운영자가 explorer 에서
    실제 체결 여부를 확인해야 중복 전송을 피할 수 있다.
    """

    status_code = 502
    code = "transaction_failed"

    def __init__(
        self,
        detail: str,
        signature: str | None = None,
        signatures: list[str] | None = None,
        **context: Any,
    ) -> None:
        submitted = list(signatures or [])
        if signature and signature not in submitted:
            submitted.insert(0, signature)
        if submitted:
            detail = (
                f"{detail} 제출된 서명이 있으므로 재전송 전에 체결 여부를 "
                f"확인할 것: {', '.join(submitted)}"
            )
        super().__init__(
            detail,
            signature=submitted[0] if submitted else None,
            submitted_signatures=submitted,
            **context,
        )


class RpcUnavailable(SolanaError):
    """RPC 노드에 도달할 수 없거나 레이트리밋에 걸렸다."""

    status_code = 503
    code = "rpc_unavailable"


class ConfigError(SolanaError):
    """필수 환경변수가 비어 있다 (USDC_MINT, *_PRIVATE_KEY 등)."""

    status_code = 503
    code = "config_error"
