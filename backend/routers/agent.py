"""에이전트 라우터 — 판단(analyze)과 실행(execute)은 반드시 분리되어 있다.

analyze  : Gemini 판단만 수행하고 자금은 전혀 움직이지 않는다.
execute  : 이미 만들어진 Decision 을 정책 재검증 후 실행한다.
run-cycle: 잔고 갱신 → analyze → (auto_execute 이면) execute 를 한 번에 돌린다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

import mock_data
from schemas import (
    AgentStatus,
    AnalyzeRequest,
    CycleResult,
    Decision,
    ExecuteRequest,
    ExecutionResult,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _select_actions(
    decision: dict[str, Any], action_indices: list[int] | None
) -> list[dict[str, Any]]:
    """실행 대상 액션을 고른다. 인덱스가 범위를 벗어나면 422."""
    actions = decision["actions"]
    if action_indices is None:
        return list(actions)

    invalid = [i for i in action_indices if i < 0 or i >= len(actions)]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"action_indices out of range: {invalid} (actions: {len(actions)})",
        )
    return [actions[i] for i in action_indices]


def _perform_execution(
    decision: dict[str, Any],
    action_indices: list[int] | None = None,
    approved_by: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Decision 실행 본체. execute 와 run-cycle 이 공유한다.

    3단계에서 services.executor.execute() 호출로 대체된다.
    """
    selected = _select_actions(decision, action_indices)
    policy = mock_data.get_policy()

    # 저장된 policy_check 를 믿지 않고 현재 정책으로 다시 검증한다.
    check = mock_data.validate_actions(selected, policy)
    if not check["passed"]:
        blocked_reason = " / ".join(check["violations"])
        result = {
            "id": mock_data.next_id("exec"),
            "decision_id": decision["id"],
            "status": "blocked",
            "transactions": [],
            "blocked_reason": blocked_reason,
        }
        mock_data.add_execution(result)
        mock_data.add_log(
            "decision",
            f"실행 차단 - {decision['id']}",
            reasoning=blocked_reason,
            status="blocked",
        )
        mock_data.add_alert(
            "warning",
            "정책 위반으로 실행이 차단됨",
            blocked_reason,
            decision_id=decision["id"],
        )
        return result

    transactions: list[dict[str, Any]] = []
    for action in selected:
        if action["type"] in ("wait", "alert"):
            continue

        amount = float(action.get("amount") or 0.0)
        tx_type = "swap" if action["type"] == "swap_and_transfer" else "transfer"
        tx = mock_data.make_tx(
            tx_type,
            amount,
            from_wallet=action.get("from_wallet"),
            to_wallet=action.get("to_wallet"),
        )
        transactions.append(tx)

        if dry_run:
            continue

        if action.get("from_wallet"):
            mock_data.adjust_balance(action["from_wallet"], -amount)
        if action.get("to_wallet"):
            mock_data.adjust_balance(action["to_wallet"], amount)
        mock_data.consume_daily(amount)
        mock_data.add_transaction(tx)
        mock_data.add_log(
            tx_type,
            f"{action.get('from_wallet')} → {action.get('to_wallet')} "
            f"{amount:,.0f} {action.get('token', 'USDC')} 이체",
            reasoning=action["reason"],
            signature=tx["signature"],
            explorer=tx["explorer_url"],
            amount=amount,
            status="confirmed",
        )

    if not dry_run:
        _mark_funded_payments()

    result = {
        "id": mock_data.next_id("exec"),
        "decision_id": decision["id"],
        "status": "simulated" if dry_run else "success",
        "transactions": transactions,
        "blocked_reason": None,
    }
    mock_data.add_execution(result)

    if not dry_run:
        mock_data.record_execution()
        mock_data.add_log(
            "decision",
            f"실행 완료 - {decision['id']}",
            reasoning=(
                f"{len(transactions)}건의 트랜잭션이 확정되었다."
                + (f" 승인자: {approved_by}." if approved_by else "")
            ),
            amount=sum(tx["amount"] for tx in transactions),
            status="success",
        )

    return result


def _mark_funded_payments() -> None:
    """충전으로 자금이 확보된 at_risk 건을 funded 로 승격시킨다."""
    risky_ids = {p["id"] for p in mock_data.at_risk_payments(72)}
    for payment in mock_data.list_payments(hours=720):
        if payment["status"] == "at_risk" and payment["id"] not in risky_ids:
            mock_data.set_payment_status(payment["id"], "funded")


@router.post("/analyze", response_model=Decision, summary="AI 판단 (자금 이동 없음)")
def analyze(body: AnalyzeRequest) -> Decision:
    """예정 지출과 잔고를 대조해 Decision 을 만든다. 자금은 움직이지 않는다."""
    if body.force_refresh:
        mock_data.refresh_balances()

    decision = mock_data.build_decision(horizon_hours=body.horizon_hours)
    mock_data.add_log(
        "decision",
        f"판단 생성 - {decision['summary']}",
        reasoning=decision["reasoning"],
        status=decision["risk_level"],
    )
    if decision["risk_level"] == "critical":
        mock_data.add_alert(
            "critical",
            "자금 부족 위험 감지",
            decision["summary"],
            decision_id=decision["id"],
        )
    return Decision(**decision)


@router.post("/execute", response_model=ExecutionResult, summary="판단 실행")
def execute(body: ExecuteRequest) -> ExecutionResult:
    """Decision 을 정책 재검증 후 실행한다.

    - 없는 decision_id 이면 404
    - 이미 자금이 움직인 decision 이면 409
    - 정책 위반이면 200 + status="blocked" + blocked_reason
    """
    decision = mock_data.get_decision(body.decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"decision '{body.decision_id}' not found")

    if not body.dry_run:
        already = mock_data.find_committed_execution(body.decision_id)
        if already is not None:
            raise HTTPException(
                status_code=409,
                detail=f"decision '{body.decision_id}' already executed as '{already['id']}'",
            )

    result = _perform_execution(
        decision,
        action_indices=body.action_indices,
        approved_by=body.approved_by,
        dry_run=body.dry_run,
    )
    return ExecutionResult(**result)


@router.post("/run-cycle", response_model=CycleResult, summary="전체 사이클 1회 실행")
def run_cycle() -> CycleResult:
    """잔고 갱신 → analyze → auto_execute 이면 execute 까지 한 바퀴 돌린다."""
    mock_data.refresh_balances()
    decision = mock_data.build_decision(horizon_hours=72)
    state = mock_data.record_cycle()
    cycle_id = mock_data.next_id("cycle")

    mock_data.add_log(
        "cycle",
        f"정기 사이클 실행 (#{state['cycles_today']})",
        reasoning=decision["summary"],
        status=decision["risk_level"],
    )

    execution: dict[str, Any] | None = None
    if mock_data.get_policy()["auto_execute"]:
        execution = _perform_execution(decision, approved_by="agent(auto)")

    return CycleResult(
        cycle_id=cycle_id,
        decision=Decision(**decision),
        execution=ExecutionResult(**execution) if execution else None,
        ran_at=state["last_cycle_at"],
    )


@router.get("/status", response_model=AgentStatus, summary="에이전트 상태")
def get_status() -> AgentStatus:
    """스케줄러 구동 여부와 오늘의 사이클/실행 횟수를 반환한다."""
    return AgentStatus(**mock_data.get_agent_state())
