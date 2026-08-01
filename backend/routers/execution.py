"""온체인 실행 라우터 — Jupiter 스왑 견적과 Solana Pay(x402) 배치 결제."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

import mock_data
from schemas import PayRequest, PayResult, RecipientResult, SwapQuote, SwapQuoteRequest

router = APIRouter(tags=["execution"])


@router.post("/api/swap/quote", response_model=SwapQuote, summary="Jupiter 스왑 견적")
def quote_swap(body: SwapQuoteRequest) -> SwapQuote:
    """스왑 예상 수령액과 price impact 를 반환하고 정책 슬리피지 한도와 대조한다."""
    quote = mock_data.quote_swap(body.input_token, body.output_token, body.amount)
    if not quote:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported token pair: {body.input_token}/{body.output_token}",
        )
    return SwapQuote(**quote)


@router.post("/api/pay", response_model=PayResult, summary="Solana Pay(x402) 배치 결제")
def pay(body: PayRequest) -> PayResult:
    """예정 지출의 수신자 전원에게 USDC 를 송금한다.

    출금 지갑 잔고가 총액에 미치지 못하면 409 를 반환한다.
    """
    payment = mock_data.get_payment(body.payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail=f"payment '{body.payment_id}' not found")

    wallet = mock_data.get_wallet(payment["source_wallet"])
    if wallet is None:
        raise HTTPException(
            status_code=422, detail=f"unknown source_wallet '{payment['source_wallet']}'"
        )

    total = float(payment["amount"])
    if wallet["usdc_balance"] < total:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{wallet['name']} 지갑 잔고 부족: "
                f"{wallet['usdc_balance']:,.0f} < {total:,.0f} USDC"
            ),
        )

    recipients: list[dict[str, Any]] = payment["recipients"] or [
        {"name": payment["title"], "address": wallet["address"], "amount": total}
    ]

    results: list[RecipientResult] = []
    for recipient in recipients:
        tx = mock_data.make_tx(
            "payment",
            recipient["amount"],
            from_wallet=payment["source_wallet"],
            to_wallet=None,
        )
        results.append(
            RecipientResult(
                name=recipient["name"],
                address=recipient["address"],
                amount=recipient["amount"],
                status="simulated" if body.dry_run else "paid",
                signature=tx["signature"],
                explorer_url=tx["explorer_url"],
            )
        )

    success_count = sum(1 for r in results if r.status in ("paid", "simulated"))
    failed_count = len(results) - success_count

    if not body.dry_run:
        # daily_used 는 에이전트가 자율 실행한 이체만 누적한다.
        # 예정 지출 결제는 운영자가 승인한 지출이므로 한도 대상이 아니다.
        mock_data.adjust_balance(payment["source_wallet"], -total)
        mock_data.set_payment_status(payment["id"], "paid")

        batch_tx = mock_data.make_tx(
            "payment", total, from_wallet=payment["source_wallet"], to_wallet=None
        )
        mock_data.add_transaction(batch_tx)
        mock_data.add_log(
            "payment",
            f"Solana Pay 배치 결제 - {payment['title']}",
            reasoning=(
                f"수신자 {len(results)}명에게 총 {total:,.0f} USDC 를 "
                f"{wallet['name']} 지갑에서 x402 로 송금했다."
            ),
            signature=batch_tx["signature"],
            explorer=batch_tx["explorer_url"],
            amount=total,
            status="confirmed",
        )
        mock_data.add_alert(
            "info",
            "결제 완료",
            f"'{payment['title']}' {total:,.0f} USDC 결제가 확정되었다.",
        )

    return PayResult(
        payment_id=payment["id"],
        status="simulated" if body.dry_run else ("success" if failed_count == 0 else "partial"),
        total_amount=total,
        recipient_results=results,
        success_count=success_count,
        failed_count=failed_count,
    )
