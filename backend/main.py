"""TreAgent 백엔드 진입점.

Solana 온체인 트레저리의 예정 지출을 AI 가 예측하고,
정책 검증을 거쳐 Solana Pay(x402) 로 결제까지 자율 실행하는 에이전트의 API 서버.

실행:
    uvicorn main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import mock_data
from routers import agent, dashboard, execution, logs, payments, policy, wallets
from schemas import HealthResponse

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
]

app = FastAPI(
    title="TreAgent API",
    description=(
        "Solana 온체인 트레저리 에이전트. 예정 지출을 예측하고 정책 가드레일 안에서 "
        "자금 이동과 결제를 자율 실행한다.\n\n"
        "현재 단계에서는 모든 응답이 인메모리 mock 데이터로 반환된다."
    ),
    version=mock_data.APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["health"], summary="헬스체크")
def health() -> HealthResponse:
    """서버 기동 여부와 연결된 클러스터를 반환한다."""
    return HealthResponse(
        version=mock_data.APP_VERSION,
        cluster=mock_data.CLUSTER,
        time=mock_data.utcnow(),
    )


app.include_router(dashboard.router)
app.include_router(wallets.router)
app.include_router(payments.router)
app.include_router(policy.router)
app.include_router(agent.router)
app.include_router(execution.router)
app.include_router(logs.router)
