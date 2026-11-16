"""TreAgent 백엔드 진입점.

Solana 온체인 트레저리의 예정 지출을 AI 가 예측하고,
정책 검증을 거쳐 Solana Pay(x402) 로 결제까지 자율 실행하는 에이전트의 API 서버.

실행:
    uvicorn main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import mock_data
from chain.exceptions import SolanaError
from config import settings
from routers import agent, dashboard, execution, logs, payments, policy, wallets
from schemas import HealthResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("treagent")

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """RPC 클라이언트 수명 관리 + 환경변수 점검.

    환경변수가 비어 있어도 서버는 띄운다. MOCK_MODE 로 시연하려는 경우까지
    막아버리면 폴백의 의미가 없다. 대신 startup 로그에 경고를 남긴다.
    """
    if settings.MOCK_MODE:
        logger.warning("MOCK_MODE=true — 온체인 호출 없이 mock_data 로 응답한다.")
    else:
        from chain.wallet import detect_duplicate_addresses, validate_env

        missing = validate_env()
        if missing:
            logger.warning(
                "MOCK_MODE=false 인데 환경변수가 비어 있다: %s. "
                "해당 지갑의 조회·전송은 config_error(503) 로 실패한다.",
                ", ".join(sorted(set(missing))),
            )

        for address, wallet_ids in detect_duplicate_addresses().items():
            logger.warning(
                "지갑 %s 가 같은 주소(%s)를 쓴다. .env 의 프라이빗 키가 중복됐다는 뜻이며, "
                "이 지갑들 사이의 이체는 자기 자신에게 보내는 트랜잭션이 된다.",
                " / ".join(wallet_ids),
                address,
            )

    yield

    from chain.client import close_client

    await close_client()


app = FastAPI(
    lifespan=lifespan,
    title="TreAgent API",
    description=(
        "Solana 온체인 트레저리 에이전트. 예정 지출을 예측하고 정책 가드레일 안에서 "
        "자금 이동과 결제를 자율 실행한다.\n\n"
        "잔고 조회와 USDC 전송은 Solana devnet 에 직접 연결된다. "
        "MOCK_MODE=true 로 두면 온체인 호출 없이 인메모리 mock 데이터로 응답한다."
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


@app.exception_handler(SolanaError)
async def solana_error_handler(_: Request, exc: SolanaError) -> JSONResponse:
    """온체인 레이어 예외를 HTTP 응답으로 바꾼다.

    라우터에 try/except 를 두지 않기 위한 단일 지점이다.
    InsufficientBalance/InsufficientSol -> 409, InvalidAddress -> 422,
    TransactionFailed -> 502, RpcUnavailable/ConfigError -> 503.

    exc.context 에는 비밀값이 들어가지 않는다 (chain.exceptions 참고).
    """
    if exc.status_code >= 500:
        logger.error("%s: %s", exc.code, exc.detail)
    else:
        logger.info("%s: %s", exc.code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(exc.to_dict()))


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
