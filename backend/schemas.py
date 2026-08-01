"""TreAgent Pydantic v2 스키마 정의.

온체인 트레저리 에이전트가 주고받는 모든 요청/응답 모델을 한곳에 모아둔다.
라우터는 여기 정의된 모델만 response_model 으로 사용한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Literal 타입
# --------------------------------------------------------------------------- #

WalletType = Literal["vault", "operations", "payroll", "vendor"]
PaymentType = Literal["payroll", "vendor", "api_fee", "subscription"]
PaymentStatus = Literal["scheduled", "at_risk", "funded", "paid", "failed"]
ActionType = Literal["transfer", "swap_and_transfer", "wait", "alert"]
RiskLevel = Literal["safe", "warning", "critical"]

ExecutionStatus = Literal["success", "partial", "blocked", "failed", "simulated"]
LogType = Literal["decision", "transfer", "swap", "payment", "policy", "cycle", "alert"]
AlertSeverity = Literal["info", "warning", "critical"]
TxType = Literal["transfer", "swap", "payment", "deposit"]
RecipientStatus = Literal["paid", "failed", "simulated"]


# --------------------------------------------------------------------------- #
# 코어 도메인 모델
# --------------------------------------------------------------------------- #


class Wallet(BaseModel):
    """Solana devnet 상의 트레저리 지갑 하나."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="내부 식별자 (vault, operations, ...)")
    name: str = Field(..., description="사람이 읽는 지갑 이름")
    type: WalletType
    address: str = Field(..., description="Solana devnet 주소 (base58)")
    usdc_balance: float = Field(..., ge=0, description="USDC 잔고")
    sol_balance: float = Field(..., ge=0, description="가스비용 SOL 잔고")
    min_buffer: float = Field(..., ge=0, description="항상 남겨둬야 하는 최소 USDC")
    updated_at: datetime


class Recipient(BaseModel):
    """배치 송금의 개별 수신자."""

    name: str
    address: str
    amount: float = Field(..., gt=0)


class ScheduledPayment(BaseModel):
    """예정된 지출 한 건 (급여 배치, 벤더 정산, API 요금 등)."""

    id: str
    title: str
    type: PaymentType
    amount: float = Field(..., gt=0, description="수신자 전원 합산 금액 (USDC)")
    recipient_count: int = Field(..., ge=1)
    recipients: list[Recipient] = Field(default_factory=list)
    source_wallet: str = Field(..., description="출금될 지갑 id")
    due_at: datetime
    status: PaymentStatus


class WhitelistEntry(BaseModel):
    """정책상 송금이 허용된 주소."""

    address: str
    label: str
    added_at: datetime


class Policy(BaseModel):
    """에이전트의 자율 실행 가드레일."""

    max_single_transfer: float = Field(..., gt=0, description="1회 이체 한도 (USDC)")
    max_daily_total: float = Field(..., gt=0, description="1일 누적 이체 한도 (USDC)")
    max_slippage_bps: int = Field(..., ge=0, le=10_000, description="스왑 최대 슬리피지 (bps)")
    min_buffers: dict[str, float] = Field(
        default_factory=dict, description="지갑 id -> 최소 유지 잔고"
    )
    whitelist: list[str] = Field(default_factory=list, description="허용 주소 목록")
    auto_execute: bool = Field(..., description="정책 통과 시 승인 없이 실행할지 여부")
    daily_used: float = Field(..., ge=0, description="오늘 이미 사용한 이체 금액 (읽기 전용)")


class ProposedAction(BaseModel):
    """AI가 제안한 단일 자금 이동 액션."""

    type: ActionType
    from_wallet: Optional[str] = None
    to_wallet: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)
    token: str = "USDC"
    execute_by: Optional[datetime] = Field(
        default=None, description="이 시점까지는 실행되어야 함"
    )
    reason: str


class PolicyCheck(BaseModel):
    """제안된 액션 묶음에 대한 정책 검증 결과."""

    passed: bool
    violations: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class Decision(BaseModel):
    """Gemini 판단 결과 스냅샷. 자금 이동은 포함하지 않는다."""

    id: str
    created_at: datetime
    risk_level: RiskLevel
    summary: str
    reasoning: str
    at_risk_payments: list[str] = Field(
        default_factory=list, description="자금 부족이 예상되는 payment id 목록"
    )
    actions: list[ProposedAction] = Field(default_factory=list)
    policy_check: PolicyCheck


class TxRecord(BaseModel):
    """확정된 온체인 트랜잭션 하나."""

    signature: str
    type: TxType
    from_wallet: Optional[str] = None
    to_wallet: Optional[str] = None
    amount: float
    explorer_url: str
    confirmed_at: datetime


class ExecutionResult(BaseModel):
    """Decision 실행 결과."""

    id: str
    decision_id: str
    status: ExecutionStatus
    transactions: list[TxRecord] = Field(default_factory=list)
    blocked_reason: Optional[str] = None


class LogEntry(BaseModel):
    """대시보드 타임라인에 노출되는 감사 로그."""

    id: str
    type: LogType
    timestamp: datetime
    title: str
    reasoning: Optional[str] = None
    signature: Optional[str] = None
    explorer_url: Optional[str] = None
    amount: Optional[float] = None
    status: Optional[str] = None


class Alert(BaseModel):
    """운영자에게 올라가는 경보."""

    id: str
    severity: AlertSeverity
    title: str
    message: str
    decision_id: Optional[str] = None
    created_at: datetime
    acknowledged: bool = False


# --------------------------------------------------------------------------- #
# 응답 전용 모델
# --------------------------------------------------------------------------- #


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "treagent-backend"
    version: str
    cluster: str = "devnet"
    time: datetime


class DashboardSummary(BaseModel):
    """대시보드 상단 카드에 필요한 집계값."""

    total_usdc: float
    upcoming_72h: float = Field(..., description="72시간 내 예정 지출 합계")
    at_risk_count: int
    risk_level: RiskLevel
    headline: str = Field(..., description="한 줄 요약 문구")
    last_cycle_at: Optional[datetime] = None
    next_cycle_at: Optional[datetime] = None
    daily_used: float
    daily_limit: float


class WalletDetail(Wallet):
    """지갑 상세 + 최근 트랜잭션."""

    recent_transactions: list[TxRecord] = Field(default_factory=list)


class AgentStatus(BaseModel):
    running: bool
    interval_minutes: int = 15
    last_cycle_at: Optional[datetime] = None
    next_cycle_at: Optional[datetime] = None
    cycles_today: int = 0
    executions_today: int = 0


class CycleResult(BaseModel):
    """run-cycle 한 바퀴의 결과."""

    cycle_id: str
    decision: Decision
    execution: Optional[ExecutionResult] = None
    ran_at: datetime


class SwapQuote(BaseModel):
    input_token: str
    output_token: str
    amount: float
    estimated_output: float
    price_impact_bps: int
    route: list[str] = Field(..., description="Jupiter 라우트 경로")
    within_policy: bool
    quote_expires_at: datetime


class RecipientResult(BaseModel):
    """Solana Pay(x402) 배치 송금의 수신자별 결과."""

    name: str
    address: str
    amount: float
    status: RecipientStatus
    signature: Optional[str] = None
    explorer_url: Optional[str] = None
    error: Optional[str] = None


class PayResult(BaseModel):
    payment_id: str
    status: ExecutionStatus
    total_amount: float
    recipient_results: list[RecipientResult] = Field(default_factory=list)
    success_count: int
    failed_count: int


# --------------------------------------------------------------------------- #
# 요청 모델
# --------------------------------------------------------------------------- #


class PaymentCreate(BaseModel):
    """데모용 예정 지출 수동 등록."""

    title: str = Field(..., min_length=1)
    type: PaymentType
    amount: float = Field(..., gt=0)
    source_wallet: str
    due_at: datetime
    recipients: list[Recipient] = Field(default_factory=list)
    status: PaymentStatus = "scheduled"


class PolicyUpdate(BaseModel):
    """정책 부분 업데이트. daily_used 는 서버가 관리하므로 받지 않는다."""

    model_config = ConfigDict(extra="ignore")

    max_single_transfer: Optional[float] = Field(default=None, gt=0)
    max_daily_total: Optional[float] = Field(default=None, gt=0)
    max_slippage_bps: Optional[int] = Field(default=None, ge=0, le=10_000)
    min_buffers: Optional[dict[str, float]] = None
    whitelist: Optional[list[str]] = None
    auto_execute: Optional[bool] = None


class AnalyzeRequest(BaseModel):
    horizon_hours: int = Field(default=72, gt=0, le=720)
    force_refresh: bool = False


class ExecuteRequest(BaseModel):
    decision_id: str
    action_indices: Optional[list[int]] = Field(
        default=None, description="생략하면 Decision 의 모든 액션을 실행"
    )
    approved_by: Optional[str] = None
    dry_run: bool = False


class SwapQuoteRequest(BaseModel):
    input_token: str = "SOL"
    output_token: str = "USDC"
    amount: float = Field(..., gt=0)


class PayRequest(BaseModel):
    payment_id: str
    dry_run: bool = False
