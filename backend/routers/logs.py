"""감사 로그 / 경보 라우터."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

import mock_data
from schemas import Alert, LogEntry, LogType

router = APIRouter(tags=["logs"])


@router.get("/api/logs", response_model=list[LogEntry], summary="감사 로그 (최신순)")
def list_logs(
    limit: int = Query(default=50, gt=0, le=200),
    offset: int = Query(default=0, ge=0),
    type_filter: Optional[LogType] = Query(
        default=None, alias="type", description="로그 종류로 필터"
    ),
) -> list[LogEntry]:
    """에이전트의 판단·이체·결제·정책 변경 이력을 최신순으로 반환한다."""
    items = mock_data.list_logs(limit=limit, offset=offset, log_type=type_filter)
    return [LogEntry(**item) for item in items]


@router.get("/api/alerts", response_model=list[Alert], summary="경보 목록")
def list_alerts() -> list[Alert]:
    """운영자 확인이 필요한 경보를 최신순으로 반환한다."""
    return [Alert(**alert) for alert in mock_data.list_alerts()]
