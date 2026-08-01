"""정책(가드레일) 라우터."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import mock_data
from schemas import Policy, PolicyUpdate, WhitelistEntry

router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.get("", response_model=Policy, summary="현재 정책 조회")
def get_policy() -> Policy:
    """에이전트의 자율 실행 가드레일을 반환한다."""
    return Policy(**mock_data.get_policy())


@router.put("", response_model=Policy, summary="정책 부분 업데이트")
def update_policy(body: PolicyUpdate) -> Policy:
    """전달된 필드만 갱신한다. `daily_used` 는 서버가 관리하므로 무시된다."""
    current = mock_data.get_policy()
    patch = body.model_dump(exclude_unset=True, exclude_none=True)

    merged_single = patch.get("max_single_transfer", current["max_single_transfer"])
    merged_daily = patch.get("max_daily_total", current["max_daily_total"])
    if merged_single > merged_daily:
        raise HTTPException(
            status_code=422,
            detail=(
                f"max_single_transfer({merged_single:,.0f}) 는 "
                f"max_daily_total({merged_daily:,.0f}) 보다 클 수 없다"
            ),
        )

    updated = mock_data.update_policy(patch)
    mock_data.add_log(
        "policy",
        "정책 변경",
        reasoning="운영자가 정책을 수정했다: " + ", ".join(sorted(patch.keys())),
        status="applied",
    )
    return Policy(**updated)


@router.get("/whitelist", response_model=list[WhitelistEntry], summary="화이트리스트 조회")
def get_whitelist() -> list[WhitelistEntry]:
    """송금이 허용된 주소 목록을 라벨과 함께 반환한다."""
    return [WhitelistEntry(**entry) for entry in mock_data.get_whitelist()]
