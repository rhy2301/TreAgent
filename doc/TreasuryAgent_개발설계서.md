# TreasuryAgent 개발 설계서
> 프론트엔드 개발자를 위한 풀스택 해커톤 개발 가이드

---

## 0. 전체 그림 먼저 이해하기

프론트엔드만 했다면 지금까지는 이랬을 거야.

```
브라우저 (React)
  → 어딘가 있는 API 서버에 fetch
  → 데이터 받아서 화면에 표시
```

이번에는 그 "어딘가 있는 API 서버"를 직접 만드는 거야.

```
브라우저 (React)          ← 네가 만든다 (익숙함)
    ↕  HTTP 요청/응답
백엔드 (Python FastAPI)   ← 네가 만든다 (새로 배움)
    ↕                ↕
Gemini API        Solana 네트워크
(Google Cloud)    (블록체인)
```

백엔드가 하는 일:
- 지갑 잔고 조회
- Gemini한테 "지금 결제해야 해?" 물어보기
- Solana에 트랜잭션 전송
- 프론트에 결과 돌려주기

---

## 1. 기술 스택

| 영역 | 기술 | 이유 |
|---|---|---|
| 프론트엔드 | React + Vite | 네가 아는 것 |
| 백엔드 | Python + FastAPI | Gemini·Solana Python 라이브러리가 제일 잘 되어 있음 |
| Solana | solders + solana-py | Python에서 Solana 트랜잭션 처리 |
| AI | google-generativeai | Gemini API 공식 Python SDK |
| 프론트↔백엔드 통신 | REST API (HTTP) | WebSocket보다 단순, 해커톤엔 충분 |
| 스타일 | Tailwind CSS | 빠른 UI 구성 |

**Python을 선택한 이유**: Gemini SDK와 Solana SDK 모두 Python이 가장 안정적이고 예제가 많음. Node.js로도 되지만 해커톤 속도 면에서 Python이 유리.

---

## 2. 전체 아키텍처

```
┌─────────────────────────────────────────────────────┐
│                   브라우저                           │
│  ┌──────────────────────────────────────────────┐   │
│  │           React 대시보드 (Vite)               │   │
│  │  지갑 현황 | 에이전트 로그 | 트랜잭션 이력     │   │
│  └──────────────────┬───────────────────────────┘   │
└─────────────────────│───────────────────────────────┘
                      │ HTTP (fetch/axios)
                      │ localhost:8000
┌─────────────────────▼───────────────────────────────┐
│              Python FastAPI 서버                     │
│                                                     │
│  /api/status      → 전체 상태 조회                   │
│  /api/wallets     → 지갑 잔고 목록                   │
│  /api/agent/run   → 에이전트 수동 실행 트리거          │
│  /api/logs        → 실행 로그 조회                   │
│  /api/transactions→ 트랜잭션 이력                    │
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │monitor.py│  │decision.py│  │   executor.py    │  │
│  │잔고 조회  │→│Gemini 판단│→│Solana 트랜잭션   │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
└──────────────────┬──────────────────┬───────────────┘
                   │                  │
        ┌──────────▼──┐    ┌──────────▼──────┐
        │  Gemini API  │    │  Solana Devnet   │
        │ (Google)     │    │  RPC 노드         │
        └─────────────┘    └─────────────────┘
```

---

## 3. 파일 구조

```
treasury-agent/
│
├── backend/
│   ├── main.py              # FastAPI 앱 진입점, API 라우트 정의
│   ├── config.py            # 환경변수, 상수 관리
│   ├── mock_data.py         # 예정 결제 스케줄 (하드코딩 mock)
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── monitor.py       # 지갑 잔고 조회
│   │   ├── decision.py      # Gemini 판단 엔진
│   │   └── executor.py      # Solana 트랜잭션 실행
│   │
│   ├── solana/
│   │   ├── __init__.py
│   │   ├── wallet.py        # 지갑 생성/관리
│   │   └── transfer.py      # USDC 전송, Solana Pay
│   │
│   └── requirements.txt     # Python 패키지 목록
│
├── frontend/
│   ├── src/
│   │   ├── main.jsx
│   │   ├── App.jsx
│   │   ├── api/
│   │   │   └── client.js    # 백엔드 API 호출 함수 모음
│   │   └── components/
│   │       ├── WalletPanel.jsx       # 지갑별 잔고 카드
│   │       ├── AgentLog.jsx          # 에이전트 판단 로그
│   │       ├── PaymentSchedule.jsx   # 예정 결제 타임라인
│   │       └── TransactionHistory.jsx # 트랜잭션 이력 + 해시 링크
│   │
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
│
├── .env                     # API 키, 지갑 키 (절대 git에 올리면 안 됨)
├── .gitignore
└── README.md
```

---

## 4. 개발 순서 (단계별)

해커톤에서 가장 중요한 원칙: **동작하는 가장 작은 것부터 만들고, 살을 붙인다.**

```
Phase 0: 환경 세팅       (0.5일)
Phase 1: Solana 지갑 + 송금  (1.5일)  ← 핵심 기반
Phase 2: Gemini 판단 엔진    (1일)
Phase 3: 에이전트 루프 연결   (1일)
Phase 4: FastAPI 서버 + 프론트 연결  (1일)
Phase 5: 프론트 대시보드     (2일)
Phase 6: Solana Pay 결제     (1일)
Phase 7: 데모 polish        (0.5일)
```

---

## Phase 0: 환경 세팅

### 0-1. 프로젝트 폴더 생성

```bash
mkdir treasury-agent
cd treasury-agent
mkdir backend frontend
```

### 0-2. Python 가상환경 설정

가상환경이란: 이 프로젝트에서만 쓰는 Python 패키지 공간. 다른 프로젝트랑 충돌 안 나게.

```bash
cd backend
python3 -m venv venv

# Mac/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate

# 프롬프트 앞에 (venv) 뜨면 성공
```

### 0-3. Python 패키지 설치

```bash
pip install fastapi uvicorn python-dotenv google-generativeai solders solana httpx
```

`requirements.txt` 생성:
```
fastapi
uvicorn
python-dotenv
google-generativeai
solders
solana
httpx
```

### 0-4. 프론트엔드 세팅

```bash
cd ../frontend
npm create vite@latest . -- --template react
npm install
npm install axios tailwindcss @tailwindcss/vite
```

### 0-5. .env 파일 생성

프로젝트 루트(treasury-agent/)에:

```
# .env
GEMINI_API_KEY=여기에_구글_API_키
SOLANA_RPC_URL=https://api.devnet.solana.com
TREASURY_VAULT_PRIVATE_KEY=여기에_지갑_프라이빗_키
PAYROLL_WALLET_PRIVATE_KEY=여기에_지갑_프라이빗_키
OPERATIONS_WALLET_PRIVATE_KEY=여기에_지갑_프라이빗_키
```

`.gitignore`에 반드시 추가:
```
.env
venv/
__pycache__/
node_modules/
```

---

## Phase 1: Solana 지갑 + USDC 송금

이게 제일 낯선 부분. 단계적으로.

### 1-1. Solana Devnet 지갑 3개 만들기

`backend/solana/wallet.py`:

```python
from solders.keypair import Keypair
import base58

def create_wallet():
    """새 지갑 생성"""
    kp = Keypair()
    private_key = base58.b58encode(bytes(kp)).decode()
    public_key = str(kp.pubkey())
    return {"public_key": public_key, "private_key": private_key}

if __name__ == "__main__":
    # 이 파일 직접 실행하면 지갑 3개 생성됨
    wallets = ["treasury_vault", "payroll_wallet", "operations_wallet"]
    for name in wallets:
        w = create_wallet()
        print(f"\n{name}:")
        print(f"  Public:  {w['public_key']}")
        print(f"  Private: {w['private_key']}")
        print(f"  → .env에 PRIVATE_KEY로 저장하고, Public Key는 Devnet Faucet에서 SOL 받기")
```

실행:
```bash
python solana/wallet.py
```

출력된 Public Key를 https://faucet.solana.com 에서 각각 2 SOL 받기.

### 1-2. 잔고 조회

`backend/agent/monitor.py`:

```python
import asyncio
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

SOLANA_RPC = "https://api.devnet.solana.com"

async def get_sol_balance(public_key: str) -> float:
    """SOL 잔고 조회 (lamports → SOL 변환)"""
    async with AsyncClient(SOLANA_RPC) as client:
        pubkey = Pubkey.from_string(public_key)
        resp = await client.get_balance(pubkey)
        lamports = resp.value
        return lamports / 1_000_000_000  # 1 SOL = 10^9 lamports

async def get_all_balances(wallets: dict) -> dict:
    """여러 지갑 잔고 한 번에 조회"""
    result = {}
    for name, address in wallets.items():
        balance = await get_sol_balance(address)
        result[name] = {"address": address, "balance_sol": balance}
    return result

# 테스트
if __name__ == "__main__":
    wallets = {
        "treasury_vault": "여기에_Public_Key",
        "payroll_wallet": "여기에_Public_Key",
    }
    balances = asyncio.run(get_all_balances(wallets))
    print(balances)
```

### 1-3. SOL 전송 테스트

`backend/solana/transfer.py`:

```python
import asyncio
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.system_program import transfer, TransferParams
from solders.message import Message
import base58

SOLANA_RPC = "https://api.devnet.solana.com"

async def send_sol(from_private_key: str, to_address: str, amount_sol: float) -> str:
    """SOL 전송. 성공 시 트랜잭션 해시 반환."""
    from_keypair = Keypair.from_base58_string(from_private_key)
    to_pubkey = Pubkey.from_string(to_address)
    lamports = int(amount_sol * 1_000_000_000)

    async with AsyncClient(SOLANA_RPC) as client:
        blockhash_resp = await client.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        ix = transfer(TransferParams(
            from_pubkey=from_keypair.pubkey(),
            to_pubkey=to_pubkey,
            lamports=lamports
        ))

        msg = Message.new_with_blockhash([ix], from_keypair.pubkey(), blockhash)
        tx = Transaction([from_keypair], msg, blockhash)

        resp = await client.send_transaction(tx)
        tx_hash = str(resp.value)
        print(f"트랜잭션 완료: https://explorer.solana.com/tx/{tx_hash}?cluster=devnet")
        return tx_hash
```

**여기까지 되면 Solana 핵심 기능 완성.**
MVP에서는 USDC 대신 SOL로 대체해도 데모 가능.
(USDC SPL Token은 별도 설정 필요 — Phase 6에서 추가)

---

## Phase 2: Gemini 판단 엔진

`backend/agent/decision.py`:

```python
import google.generativeai as genai
import json
import os

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

def ask_gemini(wallets: dict, scheduled_payments: list, policy: dict) -> dict:
    """
    Gemini에게 현재 상황 전달 → 실행 여부 + 근거 받기

    반환 형식:
    {
        "should_execute": true/false,
        "from_wallet": "treasury_vault",
        "to_wallet": "payroll_wallet",
        "amount": 25000,
        "token": "SOL",
        "reason": "금요일 급여 28,000 SOL 지급 예정. 현재 잔고 3,200. 부족분 이체 필요.",
        "swap_needed": false
    }
    """

    prompt = f"""
당신은 트레저리 관리 AI 에이전트입니다.
아래 정보를 바탕으로 자금 이동이 필요한지 판단하고, JSON으로만 응답하세요.

## 현재 지갑 잔고
{json.dumps(wallets, ensure_ascii=False, indent=2)}

## 향후 72시간 예정 결제
{json.dumps(scheduled_payments, ensure_ascii=False, indent=2)}

## 정책 제약
{json.dumps(policy, ensure_ascii=False, indent=2)}

## 판단 기준
1. 예정 결제를 수행하기에 잔고가 충분한가?
2. 부족하다면 어디서 얼마를 이동해야 하는가?
3. 직접 전송으로 충분한가, 토큰 스왑이 필요한가?
4. 정책 한도 내에서 실행 가능한가?

반드시 아래 JSON 형식으로만 응답:
{{
  "should_execute": true 또는 false,
  "from_wallet": "지갑명 또는 null",
  "to_wallet": "지갑명 또는 null",
  "amount": 숫자 또는 0,
  "token": "SOL 또는 USDC",
  "reason": "판단 근거 (한국어, 2-3문장)",
  "swap_needed": true 또는 false,
  "risk_flag": "none 또는 low 또는 high"
}}
"""

    response = model.generate_content(prompt)
    text = response.text.strip()

    # JSON 파싱 (Gemini가 ```json 블록으로 감쌀 수 있음)
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    return json.loads(text)


# 테스트
if __name__ == "__main__":
    wallets = {
        "treasury_vault": {"balance": 80000, "token": "SOL"},
        "payroll_wallet": {"balance": 3200, "token": "SOL"},
    }
    scheduled_payments = [
        {"name": "기여자 급여", "amount": 28000, "due_in_hours": 38, "to": "payroll_wallet"}
    ]
    policy = {
        "max_single_transfer": 50000,
        "daily_limit": 100000,
        "allowed_wallets": ["treasury_vault", "payroll_wallet", "operations_wallet"]
    }

    result = ask_gemini(wallets, scheduled_payments, policy)
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

---

## Phase 3: 에이전트 루프 연결

`backend/agent/executor.py`:

```python
import asyncio
from datetime import datetime
from .monitor import get_all_balances
from .decision import ask_gemini
from ..solana.transfer import send_sol
from ..mock_data import SCHEDULED_PAYMENTS, POLICY, WALLET_ADDRESSES
import os

execution_logs = []  # 실행 로그 (메모리에 저장, DB 없이 MVP)

async def run_agent_cycle() -> dict:
    """에이전트 1사이클 실행. 감지 → 판단 → 실행."""

    log_entry = {"timestamp": datetime.now().isoformat(), "steps": []}

    # 1. 잔고 조회
    balances = await get_all_balances(WALLET_ADDRESSES)
    log_entry["steps"].append({"step": "monitor", "data": balances})

    # 2. Gemini 판단
    decision = ask_gemini(balances, SCHEDULED_PAYMENTS, POLICY)
    log_entry["steps"].append({"step": "decision", "data": decision})

    # 3. 실행 여부 판단
    if not decision["should_execute"]:
        log_entry["status"] = "skipped"
        log_entry["reason"] = decision["reason"]
        execution_logs.append(log_entry)
        return log_entry

    # 4. 정책 검증
    amount = decision["amount"]
    if amount > POLICY["max_single_transfer"]:
        log_entry["status"] = "blocked"
        log_entry["reason"] = f"한도 초과: {amount} > {POLICY['max_single_transfer']}"
        execution_logs.append(log_entry)
        return log_entry

    # 5. 트랜잭션 실행
    from_key = os.getenv(f"{decision['from_wallet'].upper()}_PRIVATE_KEY")
    to_address = WALLET_ADDRESSES[decision["to_wallet"]]

    tx_hash = await send_sol(from_key, to_address, amount)
    log_entry["status"] = "executed"
    log_entry["tx_hash"] = tx_hash
    log_entry["reason"] = decision["reason"]
    log_entry["steps"].append({"step": "execute", "tx_hash": tx_hash})

    execution_logs.append(log_entry)
    return log_entry
```

`backend/mock_data.py`:

```python
WALLET_ADDRESSES = {
    "treasury_vault":    "여기에_Public_Key",
    "payroll_wallet":    "여기에_Public_Key",
    "operations_wallet": "여기에_Public_Key",
}

SCHEDULED_PAYMENTS = [
    {
        "name": "기여자 급여 (14명)",
        "amount": 28000,
        "token": "SOL",
        "due_in_hours": 38,
        "recipients": ["addr1", "addr2"],  # 실제 주소로 교체
        "to_wallet": "payroll_wallet"
    },
    {
        "name": "클라우드 인프라 비용",
        "amount": 3000,
        "token": "SOL",
        "due_in_hours": 72,
        "to_wallet": "operations_wallet"
    }
]

POLICY = {
    "max_single_transfer": 50000,
    "daily_limit": 100000,
    "min_buffer": 2000,
    "allowed_wallets": ["treasury_vault", "payroll_wallet", "operations_wallet"],
    "max_slippage_percent": 0.5
}
```

---

## Phase 4: FastAPI 서버

`backend/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from agent.executor import run_agent_cycle, execution_logs
from agent.monitor import get_all_balances
from mock_data import WALLET_ADDRESSES, SCHEDULED_PAYMENTS, POLICY

app = FastAPI()

# CORS 설정: 프론트(localhost:5173)에서 백엔드(localhost:8000)로 요청 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/status")
async def get_status():
    """전체 상태 요약"""
    balances = await get_all_balances(WALLET_ADDRESSES)
    return {
        "wallets": balances,
        "scheduled_payments": SCHEDULED_PAYMENTS,
        "policy": POLICY,
        "last_run": execution_logs[-1] if execution_logs else None
    }

@app.get("/api/wallets")
async def get_wallets():
    return await get_all_balances(WALLET_ADDRESSES)

@app.post("/api/agent/run")
async def trigger_agent():
    """에이전트 수동 실행 (데모용 버튼)"""
    result = await run_agent_cycle()
    return result

@app.get("/api/logs")
async def get_logs():
    return {"logs": execution_logs[-20:]}  # 최근 20개

@app.get("/api/transactions")
async def get_transactions():
    executed = [log for log in execution_logs if log.get("tx_hash")]
    return {"transactions": executed}
```

서버 실행:
```bash
cd backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

브라우저에서 http://localhost:8000/docs 열면 자동 생성된 API 문서 확인 가능.

---

## Phase 5: 프론트엔드 대시보드

### API 클라이언트

`frontend/src/api/client.js`:

```javascript
import axios from 'axios'

const BASE = 'http://localhost:8000'

export const api = {
  getStatus: () => axios.get(`${BASE}/api/status`),
  getWallets: () => axios.get(`${BASE}/api/wallets`),
  runAgent: () => axios.post(`${BASE}/api/agent/run`),
  getLogs: () => axios.get(`${BASE}/api/logs`),
  getTransactions: () => axios.get(`${BASE}/api/transactions`),
}
```

### 메인 앱

`frontend/src/App.jsx`:

```jsx
import { useState, useEffect } from 'react'
import { api } from './api/client'
import WalletPanel from './components/WalletPanel'
import AgentLog from './components/AgentLog'
import PaymentSchedule from './components/PaymentSchedule'
import TransactionHistory from './components/TransactionHistory'

export default function App() {
  const [status, setStatus] = useState(null)
  const [logs, setLogs] = useState([])
  const [isRunning, setIsRunning] = useState(false)

  const fetchData = async () => {
    const [statusRes, logsRes] = await Promise.all([
      api.getStatus(),
      api.getLogs()
    ])
    setStatus(statusRes.data)
    setLogs(logsRes.data.logs)
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 15000) // 15초마다 자동 갱신
    return () => clearInterval(interval)
  }, [])

  const handleRunAgent = async () => {
    setIsRunning(true)
    await api.runAgent()
    await fetchData()
    setIsRunning(false)
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-2xl font-bold">TreasuryAgent</h1>
            <p className="text-gray-400 text-sm">AI 자율 트레저리 관리 · Solana Devnet</p>
          </div>
          <button
            onClick={handleRunAgent}
            disabled={isRunning}
            className="bg-purple-600 hover:bg-purple-700 disabled:opacity-50 
                       px-6 py-2 rounded-lg font-medium transition"
          >
            {isRunning ? '에이전트 실행 중...' : '▶ 에이전트 실행'}
          </button>
        </div>

        <div className="grid grid-cols-3 gap-4 mb-6">
          {status?.wallets && Object.entries(status.wallets).map(([name, data]) => (
            <WalletPanel key={name} name={name} data={data} />
          ))}
        </div>

        <div className="grid grid-cols-2 gap-4 mb-6">
          <PaymentSchedule payments={status?.scheduled_payments || []} />
          <AgentLog logs={logs} />
        </div>

        <TransactionHistory />
      </div>
    </div>
  )
}
```

### 지갑 패널 컴포넌트

`frontend/src/components/WalletPanel.jsx`:

```jsx
const WALLET_LABELS = {
  treasury_vault: '트레저리 Vault',
  payroll_wallet: '급여 지갑',
  operations_wallet: '운영 지갑',
}

export default function WalletPanel({ name, data }) {
  const isLow = data.balance_sol < 5000  // 임계치 예시

  return (
    <div className={`rounded-xl p-4 border ${
      isLow ? 'border-red-500 bg-red-950/30' : 'border-gray-800 bg-gray-900'
    }`}>
      <div className="text-sm text-gray-400 mb-1">{WALLET_LABELS[name] || name}</div>
      <div className={`text-2xl font-bold ${isLow ? 'text-red-400' : 'text-white'}`}>
        {data.balance_sol?.toLocaleString()} SOL
      </div>
      {isLow && (
        <div className="text-xs text-red-400 mt-1">⚠ 잔고 부족 감지</div>
      )}
      <div className="text-xs text-gray-600 mt-2 truncate">{data.address}</div>
    </div>
  )
}
```

### 에이전트 로그 컴포넌트

`frontend/src/components/AgentLog.jsx`:

```jsx
export default function AgentLog({ logs }) {
  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h2 className="text-sm font-medium text-gray-400 mb-3">에이전트 판단 로그</h2>
      <div className="space-y-3 max-h-64 overflow-y-auto">
        {logs.length === 0 && (
          <div className="text-gray-600 text-sm">아직 실행 이력 없음</div>
        )}
        {[...logs].reverse().map((log, i) => (
          <div key={i} className="text-sm border-l-2 pl-3 border-gray-700">
            <div className="text-gray-500 text-xs">{log.timestamp}</div>
            <div className={`font-medium ${
              log.status === 'executed' ? 'text-green-400' :
              log.status === 'blocked' ? 'text-red-400' : 'text-yellow-400'
            }`}>
              {log.status === 'executed' ? '✓ 실행됨' :
               log.status === 'blocked' ? '✗ 한도 차단' : '– 대기'}
            </div>
            <div className="text-gray-300 mt-0.5">{log.reason}</div>
            {log.tx_hash && (
              <a
                href={`https://explorer.solana.com/tx/${log.tx_hash}?cluster=devnet`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-purple-400 text-xs hover:underline"
              >
                Solana Explorer에서 확인 →
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
```

---

## Phase 6: Solana Pay 결제 실행

이전까지는 지갑 간 자금 이동. 이제 실제 수신자에게 결제하는 단계.

`backend/solana/transfer.py`에 추가:

```python
async def execute_payroll(payroll_wallet_private_key: str, recipients: list) -> list:
    """
    급여 지갑에서 여러 수신자에게 일괄 송금.
    recipients = [{"address": "...", "amount": 2000}, ...]
    """
    tx_hashes = []
    for r in recipients:
        tx_hash = await send_sol(
            from_private_key=payroll_wallet_private_key,
            to_address=r["address"],
            amount_sol=r["amount"]
        )
        tx_hashes.append({"address": r["address"], "amount": r["amount"], "tx_hash": tx_hash})
        await asyncio.sleep(0.5)  # RPC 요청 간격
    return tx_hashes
```

**Solana Pay (x402) 연동**: pay.sh의 x402 프로토콜은 HTTP 헤더에 결제 정보를 담아 처리. MVP에서는 위의 직접 전송으로 동일한 효과를 시연하고, x402 포맷을 로그에 기록하는 방식으로 구현해도 심사 통과 가능.

---

## Phase 7: 데모 준비

### 데모 시나리오 스크립트

```
1. 대시보드 열기 (지갑 현황 보여주기)
   → "급여 지갑 잔고: 3,200 SOL, 다음 급여 지급까지 38시간"

2. '에이전트 실행' 버튼 클릭
   → 에이전트 로그에 Gemini 판단 실시간 표시

3. 실행 완료 (2~3초)
   → 급여 지갑 잔고 변경 확인
   → 트랜잭션 해시 클릭 → Solana Explorer에서 실제 확인

4. "사람이 한 일: 버튼 한 번" 강조
```

### 3분 데모 영상 구성

```
0:00 - 0:30  문제 제시 (기존 수동 프로세스)
0:30 - 1:30  대시보드 시연 (지갑 현황, 예정 결제)
1:30 - 2:30  에이전트 실행 + Gemini 판단 + 트랜잭션
2:30 - 3:00  Solana Explorer 확인 + 마무리
```

---

## 자주 막히는 포인트

| 상황 | 해결 |
|---|---|
| `CORS 오류` | FastAPI에 CORSMiddleware 설정 확인 |
| `solana RPC 연결 안 됨` | devnet URL 확인: `https://api.devnet.solana.com` |
| `트랜잭션 실패` | 잔고 확인 (SOL이 수수료용으로 최소 0.01 SOL 필요) |
| `Gemini JSON 파싱 오류` | 프롬프트에 "반드시 JSON만 응답" 강조, try/except 추가 |
| `venv 활성화 안 됨` | `source venv/bin/activate` 매 터미널 세션마다 실행 |
| `프론트에서 API 안 불림` | 백엔드 서버 켜져 있는지, 포트 8000 확인 |

---

## 개발 중 터미널 2개 유지

```bash
# 터미널 1 (백엔드)
cd backend
source venv/bin/activate
uvicorn main:app --reload --port 8000

# 터미널 2 (프론트엔드)
cd frontend
npm run dev
# → http://localhost:5173 에서 확인
```

---

## 체크리스트

### Phase 1 완료 기준
- [ ] Solana Devnet 지갑 3개 생성
- [ ] faucet.solana.com에서 SOL 수령 확인
- [ ] 잔고 조회 코드 실행 → 숫자 출력
- [ ] SOL 전송 테스트 → Solana Explorer에서 트랜잭션 확인

### Phase 2 완료 기준
- [ ] Gemini API 키 발급
- [ ] decision.py 실행 → JSON 판단 결과 출력

### Phase 3 완료 기준
- [ ] run_agent_cycle() 실행 → 감지 → 판단 → 실행 전체 동작

### Phase 4 완료 기준
- [ ] uvicorn 실행 후 http://localhost:8000/docs 접속
- [ ] /api/agent/run POST 호출 → 트랜잭션 발생

### Phase 5 완료 기준
- [ ] 프론트에서 지갑 잔고 표시
- [ ] 에이전트 실행 버튼 클릭 → 로그 표시
- [ ] 트랜잭션 해시 클릭 → Solana Explorer 이동

### 데모 완료 기준
- [ ] 버튼 누르면 실제 트랜잭션 발생
- [ ] Solana Explorer에서 확인 가능
- [ ] Gemini 판단 근거 화면에 표시
- [ ] 3분 데모 영상 녹화 완료
