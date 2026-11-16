"""예정 지출 라우터."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

import mock_data
from schemas import PaymentCreate, PaymentStatus, ScheduledPayment

router = APIRouter(prefix="/api/payments", tags=["payments"])


# NOTE: /upcoming 은 /{payment_id} 보다 먼저 선언되어야 경로가 가로채이지 않는다.
@router.get("/upcoming", response_model=list[ScheduledPayment], summary="예정 지출 목록")
def list_upcoming(
    hours: int = Query(default=72, gt=0, le=720, description="조회 구간 (시간)"),
    wallet_id: str | None = Query(default=None, description="출금 지갑 id 로 필터"),
    status_filter: PaymentStatus | None = Query(
        default=None, alias="status", description="상태로 필터"
    ),
) -> list[ScheduledPayment]:
    """지정 구간 내 예정 지출을 due_at 오름차순으로 반환한다."""
    items = mock_data.list_payments(hours=hours, wallet_id=wallet_id, status=status_filter)
    return [ScheduledPayment(**item) for item in items]


@router.get("/{payment_id}", response_model=ScheduledPayment, summary="예정 지출 상세")
def get_payment(payment_id: str) -> ScheduledPayment:
    """수신자 목록을 모두 포함한 상세를 반환한다. 없으면 404."""
    payment = mock_data.get_payment(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail=f"payment '{payment_id}' not found")
    return ScheduledPayment(**payment)


@router.post(
    "",
    response_model=ScheduledPayment,
    status_code=status.HTTP_201_CREATED,
    summary="예정 지출 수동 추가 (데모용)",
)
def create_payment(body: PaymentCreate) -> ScheduledPayment:
    """데모 중 시나리오를 늘리기 위해 예정 지출을 직접 등록한다."""
    if mock_data.get_wallet(body.source_wallet) is None:
        raise HTTPException(
            status_code=422, detail=f"unknown source_wallet '{body.source_wallet}'"
        )

    recipients = [r.model_dump() for r in body.recipients]
    created = mock_data.add_payment(
        {
            "title": body.title,
            "type": body.type,
            "amount": body.amount,
            "recipient_count": len(recipients) or 1,
            "recipients": recipients,
            "source_wallet": body.source_wallet,
            "due_at": body.due_at,
            "status": body.status,
        }
    )
    mock_data.add_log(
        "policy",
        f"예정 지출 등록 - {created['title']}",
        reasoning=f"{created['source_wallet']} 지갑에서 {created['amount']:,.0f} USDC 지출 예정.",
        amount=created["amount"],
        status="scheduled",
    )
    return ScheduledPayment(**created)
