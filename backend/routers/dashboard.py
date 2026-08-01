"""대시보드 집계 라우터."""

from __future__ import annotations

from fastapi import APIRouter

import mock_data
from schemas import DashboardSummary, RiskLevel

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _risk_level(at_risk_count: int, total: float, upcoming: float) -> RiskLevel:
    """at_risk 건수와 전체 커버리지로 위험도를 매긴다."""
    if at_risk_count > 0:
        return "critical"
    if total < upcoming * 1.5:
        return "warning"
    return "safe"


@router.get("/summary", response_model=DashboardSummary, summary="대시보드 요약")
def get_summary() -> DashboardSummary:
    """총 USDC, 72시간 예정 지출, at_risk 건수, 위험도, 사이클 시각을 한 번에 반환한다."""
    total = mock_data.total_usdc()
    upcoming = mock_data.upcoming_total(72)
    risky = mock_data.at_risk_payments(72)
    policy = mock_data.get_policy()
    state = mock_data.get_agent_state()

    risk_level = _risk_level(len(risky), total, upcoming)
    if risky:
        first = risky[0]
        wallet = mock_data.get_wallet(first["source_wallet"])
        wallet_name = wallet["name"] if wallet else first["source_wallet"]
        headline = (
            f"{wallet_name} 지갑이 '{first['title']}' 지급액 "
            f"{first['amount']:,.0f} USDC 에 미달한다. 즉시 충전이 필요하다."
        )
    else:
        headline = f"72시간 내 예정 지출 {upcoming:,.0f} USDC 를 현재 잔고로 모두 커버할 수 있다."

    return DashboardSummary(
        total_usdc=round(total, 2),
        upcoming_72h=round(upcoming, 2),
        at_risk_count=len(risky),
        risk_level=risk_level,
        headline=headline,
        last_cycle_at=state["last_cycle_at"],
        next_cycle_at=state["next_cycle_at"],
        daily_used=policy["daily_used"],
        daily_limit=policy["max_daily_total"],
    )
