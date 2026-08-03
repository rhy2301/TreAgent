# TreAgent
AI-based Financial Agent, Autonomous Treasury Settlement Agent

<aside>

**Treasury Agent는 Web3 기업이나 DAO의 여러 온체인 지갑을 AI가 모니터링하고, 예정된 급여·운영비·벤더 비용을 미리 예측해 필요한 자금을 자동 배치하고 결제하는 AI 기반 Web3 재무 에이전트다.**

예를 들어, 급여 지갑에 3,200 USDC가 있지만 38시간 뒤 28,000 USDC의 급여 지급이 예정되어 있다면, Gemini가 부족분과 안전 버퍼를 계산해 Treasury Vault에서 급여 지갑으로 USDC를 자동 이체한다. 지급일이 되면 기여자나 벤더에게 실제 결제까지 실행한다.

</aside>

### **핵심 기능**

1. 각 온체인 지갑의 잔액과 예정 지출을 주기적으로 확인한다.
2. Gemini가 향후 72시간의 지출과 잔액 부족 위험을 분석한다.
3. 필요한 금액, 실행 시점, 결제 경로를 결정한다.
4. USDC가 충분하면 SPL 토큰을 직접 전송한다.
5. SOL 등 다른 자산만 있다면 Jupiter를 통해 USDC로 스왑한다.
6. 정책 한도와 화이트리스트를 확인한 뒤 에이전트가 직접 트랜잭션을 서명·실행한다.
7. 트랜잭션 해시와 AI의 판단 근거를 대시보드에 기록한다.

---

### **주요 대상**

- 여러 온체인 지갑을 관리하는 Web3 스타트업
- DAO 재무팀
- 크립토 결제 서비스 운영사

---

### **해결하려는 문제**

현재 Web3 조직은 급여 지갑이나 운영 지갑의 잔액을 사람이 직접 확인하고, 부족한 금액을 계산해 수동으로 이체하는 경우가 많다. 이 때문에 야간·주말 대응이 어렵고, 잔액 부족으로 결제가 실패할 수 있다.

Treasury Agent는 예정 지출을 사전에 파악하고 필요한 자금을 자동으로 배치해 이러한 문제를 줄인다.

---

### **차별점**

기존 트레저리 서비스가 주로 잔액 조회나 수동 이체에 그친다면, Treasury Agent는 다음 과정을 하나로 연결한다.

<aside>

**지출 예측 → AI 판단 → 자금 재배치 → 실제 결제 → 온체인 기록**

</aside>

즉, 단순한 지갑 대시보드가 아니라 **AI가 정책 범위 안에서 실제 자금을 집행하는 자율형 트레저리 시스템**이다.

---

### **기술 구성 (비전)**

- **Gemini / Vertex AI:** 지출 예측, 실행 여부 판단, 판단 근거 생성
- **Cloud Run:** 24시간 모니터링 에이전트 실행
- **USDC SPL Token:** 지갑 간 자금 이동과 정산
- **Jupiter:** SOL 또는 다른 토큰을 USDC로 변환할 때 사용
- **Solana:** 트랜잭션 서명, 실행 및 이력 기록
- **Solana Pay 또는 x402:** 급여·벤더·API 비용 결제 처리

> 위 목록은 프로젝트가 지향하는 전체 그림이다. 아래 [현재 구현 상태](#현재-구현-상태)에서
> 이 중 실제로 코드가 존재하고 실행 가능한 부분과, 아직 스텁(`NotImplementedError`)으로
> 남아 있는 부분을 구분한다.

---

## 목차

- [현재 구현 상태](#현재-구현-상태)
- [Prerequisites](#prerequisites)
- [Clone & Setup](#clone--setup)
- [.env 환경변수](#env-환경변수)
- [실행 경로 1 — Mock 모드](#실행-경로-1--mock-모드)
- [실행 경로 2 — AI Studio (실제 Gemini 응답)](#실행-경로-2--ai-studio-실제-gemini-응답)
- [실행 경로 3 — pay.sh Sandbox (x402 402→결제→200)](#실행-경로-3--paysh-sandbox-x402-402결제200)
- [실행 경로 4 — Solana Devnet (SPL 잔고·전송)](#실행-경로-4--solana-devnet-spl-잔고전송)
- [백엔드 API 서버 실행](#백엔드-api-서버-실행)
- [프론트엔드 실행](#프론트엔드-실행)
- [demo.py 시나리오별 실행법](#demopy-시나리오별-실행법)
- [테스트 · 린트 · 타입체크](#테스트--린트--타입체크)
- [네트워크 분리 — Devnet ≠ pay.sh Sandbox](#네트워크-분리--devnet--paysh-sandbox)
- [파일 구조](#파일-구조)
- [이 README 작성 시 실행 검증한 것 / 하지 않은 것](#이-readme-작성-시-실행-검증한-것--하지-않은-것)

---

## 현재 구현 상태

이 저장소에는 **서로 독립적으로 동작하는 두 개의 코드 경로**가 있다. 둘을 섞어서 이해하면
"API 서버에 curl 을 치면 진짜 Gemini 가 판단한다" 같은 잘못된 그림이 그려지므로 먼저 구분한다.

| 경로 | 위치 | 무엇을 하는가 | 실제로 도는 것 |
| --- | --- | --- | --- |
| **A. FastAPI 데모 API** | `backend/main.py`, `backend/routers/*`, `backend/mock_data.py` | REST API 형태의 지갑/예정지출/정책/판단/실행 CRUD | 판단(`/api/agent/analyze` 등)은 **전부 `mock_data.py` 인메모리 데이터**다. `services/decision.py`, `services/policy.py`, `services/executor.py` 는 `raise NotImplementedError("3단계에서 구현")` 상태의 스텁이다. Gemini 를 호출하지 않는다. 단, **지갑 잔고 조회·USDC 전송**(`services/solana_client.py` → `chain/`)만은 `MOCK_MODE=false` 일 때 실제 Solana devnet RPC 를 친다. |
| **B. 판단 엔진 (`treagent/judgment/`)** | `backend/treagent/judgment/*`, `backend/scripts/demo.py` | Forecaster(결정론적 부족분 계산) → Gating → LLM 공급자(mock/aistudio/paysh) 호출 → PolicyGuard 10단계 검사 → 실행 인텐트 생성 | **실제로 동작한다.** 단 진입점은 FastAPI 라우터가 아니라 `python -m scripts.demo` 뿐이다. `pytest` 154개가 이 경로를 검증한다. |
| **C. 프론트엔드** | `frontend/` | React + Vite 대시보드 | 백엔드 API 를 호출하지 않는다. **오직 `frontend/public/snapshot.<scenario>.json` 정적 파일만 읽는다** (`src/lib/useSnapshot.ts`). 이 JSON 은 `python -m scripts.demo --emit-json <path>` 로 (B) 경로가 만든다. |
| **D. 온체인 스왑/배치결제** | `backend/chain/swap.py`, `backend/chain/pay.py`, `backend/services/executor.py` | Jupiter 스왑, Solana Pay 배치 송금 | 전부 `raise NotImplementedError("4단계에서 구현")` 스텁. 호출하면 500 이 아니라 파이썬 예외로 즉시 죽는다. |

요약하면: **"Gemini 가 실제로 판단하고, x402 로 실제로 결제하고, PolicyGuard 가 실제로 막는" 데모는
`backend/scripts/demo.py` 를 통해서만 볼 수 있다.** FastAPI 서버(A)는 지갑 CRUD·devnet
잔고조회/전송 API 데모이고, 프론트엔드(C)는 그 결과를 정적 JSON 스냅샷으로만 그린다.

```
                 python -m scripts.demo --emit-json
treagent/judgment/  ───────────────────────────────►  frontend/public/snapshot.*.json
(forecaster/gating/                                          │
 guard/providers)                                             ▼
                                                    frontend (Vite dev server, 정적 fetch)

backend/main.py + routers/*  ───(mock_data 판단, 실제 devnet 잔고/전송)───►  REST API (uvicorn)
```

---

## Prerequisites

이 저장소에서 실제로 확인한 실행 환경이다 (다른 버전에서도 동작할 수 있으나 검증하지 않았다).

| 도구 | 확인된 버전 | 비고 |
| --- | --- | --- |
| Python | 3.14.0 (`backend/venv`) | `backend/mypy.ini` 는 `python_version = 3.12` 기준으로 strict 검사. 3.12 이상 권장 |
| Node.js | v22.23.0 | `frontend/package.json` 은 엔진 버전을 명시하지 않는다 |
| npm | Node 동봉 버전 | `frontend/node_modules` 는 `npm install` 로 생성 |
| [`pay` CLI](https://pay.sh) | 0.27.0 (Homebrew) | pay.sh Sandbox 경로(x402)에서만 필요. `brew install pay` |
| Solana CLI / `spl-token` CLI | — | Solana Devnet 경로(지갑 생성·SPL 토큰 발행)에서만 필요 |

---

## Clone & Setup

```bash
git clone <이 저장소 URL>
cd TreAgent

cp .env.example .env   # 아래 "env 환경변수" 절을 보고 채운다
```

### 백엔드 가상환경

```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **AI Studio 경로(실 Gemini 응답)를 쓰려면 추가 설치가 필요하다.**
> `backend/treagent/judgment/providers/aistudio.py` 는 `from google.genai import Client` 를
> import 하는데, 이는 `google-genai` 패키지가 제공하는 모듈이다. 그런데
> `backend/requirements.txt` 에는 `google-generativeai`(다른 패키지, `google.generativeai`
> 모듈)만 적혀 있고, 이 저장소의 `backend/venv` 에도 `google-genai` 는 설치돼 있지
> 않았다(`import google.genai` → `ModuleNotFoundError`, 직접 확인함). AI Studio 경로를
> 쓰려면 다음을 추가로 설치해야 한다:
> ```bash
> pip install google-genai
> ```

### 프론트엔드 의존성

```bash
cd frontend
npm install
```

---

## .env 환경변수

`.env.example` 을 `.env` 로 복사한 뒤 채운다. 아래 표는 `backend/config.py`,
`backend/chain/*`, `backend/treagent/judgment/providers/*` 코드가 실제로 읽는
동작을 기준으로 정리했다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `SOLANA_RPC_URL` | `https://api.devnet.solana.com` | Solana devnet RPC 엔드포인트 (`chain/client.py`) |
| `SOLANA_NETWORK` | `devnet` | Explorer 링크의 `?cluster=` 값으로 쓰인다 |
| `USDC_MINT` | (없음) | 직접 발행한 devnet SPL 토큰의 mint 주소. **decimals 는 반드시 6.** 비어 있으면 잔고 조회·전송 시 `ConfigError` |
| `MOCK_MODE` | `false` | `true` 면 `backend/main.py` API 가 온체인 호출 없이 `mock_data.py` 로만 응답한다 (`backend/config.py`) |
| `VAULT_PUBLIC_KEY` / `VAULT_PRIVATE_KEY` | (없음) | Treasury Vault 지갑. `PRIVATE_KEY` 는 `bytes(Keypair)` 64바이트의 base58 인코딩 (`python -m chain.wallet` 으로 생성) |
| `OPERATIONS_PUBLIC_KEY` / `OPERATIONS_PRIVATE_KEY` | (없음) | Operations 지갑 |
| `PAYROLL_PUBLIC_KEY` / `PAYROLL_PRIVATE_KEY` | (없음) | Payroll 지갑 |
| `VENDOR_PUBLIC_KEY` / `VENDOR_PRIVATE_KEY` | (없음) | Vendor 지갑 |
| `GEMINI_API_KEY` | (없음) | [실행 경로 2 — AI Studio](#실행-경로-2--ai-studio-실제-gemini-응답)에서 `AIStudioGeminiProvider` 가 읽는다 (`backend/config.py`, `providers/aistudio.py`) |
| `TREAGENT_LLM_PROVIDER` | — | **주의:** `.env.example` 에 있지만 어떤 코드에서도 `os.getenv("TREAGENT_LLM_PROVIDER")` 로 읽지 않는다 (`aistudio.py` 안의 주석 한 줄에만 등장). `scripts/demo.py` 의 공급자 선택은 오직 `--provider` CLI 인자로만 이뤄진다. |
| `PAYSH_GATEWAY_URL` | — | **주의:** 이 이름 그대로는 코드가 읽지 않는다. 실제로 읽히는 변수는 `TREAGENT_PAYSH_GATEWAY_URL` (아래 참고). |
| `GEMINI_MODEL` | — | **주의:** 이 이름 그대로는 코드가 읽지 않는다. 실제로 읽히는 변수는 `TREAGENT_GEMINI_MODEL` (아래 참고). |

`.env.example` 에는 없지만 코드가 실제로 읽는 추가 변수 (`backend/treagent/judgment/providers/*`):

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `TREAGENT_GEMINI_MODEL` | `gemini-3.5-flash` | 사용할 Gemini 모델. AI Studio·paysh 경로 공통 (`providers/aistudio.py`) |
| `TREAGENT_PAYSH_GATEWAY_URL` | `http://127.0.0.1:1402` | x402 게이트웨이 주소. `pay --sandbox gate api ... --bind` 로 띄운 주소와 일치해야 한다 (`providers/paysh.py`) |
| `TREAGENT_PAYSH_MODE` | `sandbox` | `sandbox` 또는 `local` 만 허용. `mainnet` 은 코드 경로 자체가 없다 (대회 규정) |
| `TREAGENT_PAYSH_TIMEOUT_S` | `120.0` | `pay` CLI 호출 타임아웃(초) |

지갑 4개는 `_read_env` 가 legacy 이름도 폴백으로 받는다 (`chain/wallet.py`):
`TREASURY_VAULT_PUBLIC_KEY`/`TREASURY_VAULT_PRIVATE_KEY`,
`OPERATIONS_WALLET_PUBLIC_KEY`/`OPERATIONS_WALLET_PRIVATE_KEY`,
`PAYROLL_WALLET_PUBLIC_KEY`/`PAYROLL_WALLET_PRIVATE_KEY`,
`VENDOR_WALLET_PUBLIC_KEY`/`VENDOR_WALLET_PRIVATE_KEY`.

> ⚠️ 실제 프라이빗 키·지갑 주소·API 키는 이 README 나 다른 커밋 파일에 절대 적지 않는다.
> `.env` 는 `.gitignore` 에 있어 커밋되지 않는다.

---

## 실행 경로 1 — Mock 모드

키와 네트워크 연결 없이 판단 엔진(B 경로) 전체를 재현하는 방법이다.
`treagent/judgment/providers/mock.py` 가 시나리오 JSON에 미리 박힌 응답을 그대로 돌려준다.

```bash
cd backend
source venv/bin/activate
python -m scripts.demo --scenario normal_shortfall --provider mock
```

`--emit-json` 을 주면 프론트엔드가 읽을 스냅샷을 만든다:

```bash
python -m scripts.demo --scenario normal_shortfall --provider mock \
  --emit-json ../frontend/public/snapshot.normal_shortfall.json
```

FastAPI 서버(A 경로)를 mock 으로 띄우려면 `.env` 에서 `MOCK_MODE=true` 로 두고
[백엔드 API 서버 실행](#백엔드-api-서버-실행)을 따른다 — 이 경우 devnet RPC·지갑 키가 전혀 없어도 서버가 뜬다.

**검증 여부: 실행하고 출력 확인함** (아래 [검증 로그](#이-readme-작성-시-실행-검증한-것--하지-않은-것) 참고). 외부 네트워크·자격증명이 필요 없다.

---

## 실행 경로 2 — AI Studio (실제 Gemini 응답)

`GEMINI_API_KEY` 로 `google-genai` SDK 를 통해 실제 Gemini 를 호출한다 (`providers/aistudio.py`).
x402 결제는 거치지 않는다 — 폴백/검증용 경로다.

1. [Clone & Setup](#clone--setup)의 안내대로 `pip install google-genai` 를 추가로 설치한다.
2. `.env` 에 `GEMINI_API_KEY` 를 채운다. 필요하면 `TREAGENT_GEMINI_MODEL` 로 모델을 바꾼다
   (기본값 `gemini-3.5-flash`).
3. 실행:
   ```bash
   cd backend
   source venv/bin/activate
   python -m scripts.demo --scenario normal_shortfall --provider aistudio
   ```

**검증 여부: 실행하지 않음.** 실제 `GEMINI_API_KEY` 와 외부 Gemini API 호출이 필요해
승인 없이 실행하지 않았다. 위 명령은 `--help` 출력과 `providers/aistudio.py` 소스코드로
확인한 인자·환경변수만 반영했다.

---

## 실행 경로 3 — pay.sh Sandbox (x402 402→결제→200)

이 프로젝트는 Solana **mainnet** 을 쓸 수 없어(대회 규정), 공식 Gemini x402 게이트웨이 대신
`backend/gateway/paywall.yml` 에 정의된 **자체 x402 게이트웨이**를 로컬에 띄우고, 업스트림으로
진짜 Gemini API 를 프록시한다. 402 챌린지·USDC 결제·200 응답이 전부 실제로 일어난다
(`backend/treagent/judgment/providers/paysh.py` 상단 docstring 참고).

1. `pay` CLI 설치 확인 (Prerequisites에서 확인한 버전: 0.27.0):
   ```bash
   brew install pay
   ```
2. sandbox 계정 생성/자금 확보 (Surfpool localnet 지갑):
   ```bash
   pay setup
   ```
3. `.env` 에 `GEMINI_API_KEY` 를 채운다 — 게이트웨이가 결제 검증 후 이 키를
   업스트림 Gemini 요청 헤더로 그대로 주입한다 (`paywall.yml` 의
   `routing.auth.value_from_env: GEMINI_API_KEY`). 게이트웨이 파일 자체에는 키를 적지 않는다.
4. 게이트웨이 기동 (별도 터미널, `backend/` 기준):
   ```bash
   cd backend
   set -a && source ../.env && set +a
   pay --sandbox gate api gateway/paywall.yml --bind 127.0.0.1:1402
   ```
5. 판단 사이클 실행 (원래 터미널):
   ```bash
   cd backend
   source venv/bin/activate
   python -m scripts.demo --scenario normal_shortfall --provider paysh
   ```

게이트웨이 주소를 바꾸려면 `TREAGENT_PAYSH_GATEWAY_URL` 을, 모드를 바꾸려면
`TREAGENT_PAYSH_MODE`(`sandbox`/`local`만 허용)를 설정한다.

**검증 여부: 실행하지 않음.** `pay setup`(계정 생성·자금 확보)과 게이트웨이 기동·실제
Gemini 호출이 필요해 승인 없이 실행하지 않았다. 명령어 자체는 `pay --help` /
`pay setup --help` / `pay gate api --help` 를 직접 실행해 옵션을 확인했고,
`backend/gateway/paywall.yml` 헤더 주석의 기동 명령과 대조해 작성했다.

---

## 실행 경로 4 — Solana Devnet (SPL 잔고 조회와 전송)

`chain/` 레이어가 실제 devnet RPC 를 호출한다. `MOCK_MODE=false` 일 때 FastAPI 서버(A 경로)의
지갑 API 와 `scripts/check_balances.py` 가 이 경로를 탄다.

1. 지갑 4개 생성:
   ```bash
   cd backend
   source venv/bin/activate
   python -m chain.wallet          # VAULT_/OPERATIONS_/PAYROLL_/VENDOR_ 키 출력
   ```
   출력된 줄을 `.env` 에 그대로 붙여넣는다. **지갑마다 서로 다른 키**여야 한다 — 같은 키를
   두 지갑에 넣으면 서버 시작 시 경고가 남고, 그 지갑들 사이 이체는 자기 자신에게 보내는
   무의미한 트랜잭션이 된다 (`chain/wallet.py::detect_duplicate_addresses`).
2. 가스비용 SOL 확보 (지갑당 최소 0.01 SOL):
   ```bash
   solana airdrop 2 <PUBLIC_KEY> --url devnet    # 또는 https://faucet.solana.com
   ```
3. USDC 역할을 할 SPL 토큰 발행 (**`--decimals 6` 필수** — `transfer_checked` 가 decimals 를 검증한다):
   ```bash
   spl-token create-token --decimals 6 --url devnet     # 출력된 mint 주소를 USDC_MINT 에
   spl-token create-account <MINT> --url devnet
   spl-token mint <MINT> 1000000 --url devnet
   ```
4. 연동 확인:
   ```bash
   python scripts/check_balances.py                 # 지갑 4개 표
   python scripts/check_balances.py --json           # 기계가 읽을 형태
   python scripts/check_balances.py --airdrop vault  # devnet SOL 추가 요청
   ```
5. 전송 테스트 (반드시 `--dry-run` 먼저):
   ```bash
   python scripts/check_balances.py --from vault --to payroll --amount 10 --dry-run
   python scripts/check_balances.py --from vault --to payroll --amount 10
   ```

**검증 여부: 실행하지 않음.** 실제 devnet 지갑 키·SOL·SPL 토큰 발행이 필요해 승인 없이
실행하지 않았다. 명령어는 `python scripts/check_balances.py --help` 실행 결과와
`backend/chain/wallet.py` / `backend/README.md` 소스를 대조해 작성했다.

---

## 백엔드 API 서버 실행

```bash
cd backend
source venv/bin/activate
uvicorn main:app --reload
```

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- CORS 허용 origin: `http://localhost:3000`, `http://localhost:5173` (`backend/main.py`)

`MOCK_MODE=true` 면 온체인 호출 없이 전부 `mock_data.py` 로 응답한다. mock 상태는 프로세스
메모리에만 있으므로 **서버를 재시작하면 초기화**된다. 위에서 짚었듯, 이 서버의 `/api/agent/*`
판단은 Gemini 를 호출하지 않는 mock 이며, 지갑 잔고/전송만 `MOCK_MODE=false` 에서 실제
devnet 이다. 엔드포인트 전체 목록과 상태 코드 규칙은 [backend/README.md](backend/README.md)에 있다.

**검증 여부: 서버 기동 자체는 실행하지 않음** (별도 프로세스로 계속 떠 있어야 하고, devnet
연동 여부에 따라 승인이 필요한 경로와 겹친다). `main.py` / `routers/*` 소스 읽기로 엔드포인트
목록과 CORS 설정을 확인했다.

---

## 프론트엔드 실행

프론트엔드는 백엔드 API 를 호출하지 않는다 — `frontend/public/snapshot.<scenario>.json`
정적 파일만 읽는다 (`src/lib/useSnapshot.ts`).

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

시나리오는 `?scenario=` 쿼리스트링으로 딥링크할 수 있다 (예: `?scenario=prompt_injection`).
지원 시나리오는 `normal_shortfall` / `prompt_injection` / `inference_budget_low` 세 가지다
(`frontend/src/types.ts` 의 `SCENARIOS`). 화면에 값을 채우려면 먼저
[실행 경로 1 — Mock 모드](#실행-경로-1--mock-모드)의 `--emit-json` 으로 해당 시나리오의
스냅샷을 `frontend/public/` 에 만들어 둬야 한다 — 저장소에는 세 시나리오 스냅샷이 이미
커밋돼 있다 (`frontend/public/snapshot.*.json`).

```bash
npm run build       # tsc --noEmit && vite build → frontend/dist
npm run lint         # eslint .
npm run preview      # 빌드 결과 로컬 프리뷰
```

**검증 여부: `npm run build`, `npm run lint` 실행하고 통과 확인함** (아래 [검증 로그](#이-readme-작성-시-실행-검증한-것--하지-않은-것) 참고). `npm run dev` 로 브라우저까지 띄워 확인하지는 않았다.

---

## demo.py 시나리오별 실행법

```
usage: python -m scripts.demo [-h]
                              [--scenario {normal_shortfall,prompt_injection,inference_budget_low}]
                              [--provider {paysh,aistudio,mock}]
                              [--emit-json EMIT_JSON]
```

(`python -m scripts.demo --help` 실제 출력. `--provider` 기본값은 `paysh`, `--scenario` 기본값은 `normal_shortfall`.)

| 시나리오 | 설명 (`tests/fixtures/scenarios/*.json` 의 `description`) |
| --- | --- |
| `normal_shortfall` | payroll/vendor 두 지갑이 부족하고 트레저리는 충분한 정상 경로 |
| `prompt_injection` | 지출 memo 에 심어진 인젝션이 LLM 을 흔들어 미등록 지갑으로 송금을 제안 — PolicyGuard 가 전체 거부해야 한다 |
| `inference_budget_low` | 추론 예산 잔여 일수가 정책 하한 아래 — LLM 을 부르지 않고 결정론적 폴백으로 완주해야 한다 |

```bash
cd backend
source venv/bin/activate

# mock 공급자 — 키 없이 3개 시나리오 모두 재현 가능 (검증 완료)
python -m scripts.demo --scenario normal_shortfall --provider mock
python -m scripts.demo --scenario prompt_injection --provider mock
python -m scripts.demo --scenario inference_budget_low --provider mock

# 프론트엔드용 스냅샷 생성 (검증 완료)
python -m scripts.demo --scenario normal_shortfall --provider mock \
  --emit-json ../frontend/public/snapshot.normal_shortfall.json

# 실 Gemini / x402 — GEMINI_API_KEY, pay.sh 게이트웨이 필요 (미실행, 위 실행 경로 2·3 참고)
python -m scripts.demo --scenario normal_shortfall --provider aistudio
python -m scripts.demo --scenario normal_shortfall --provider paysh
```

터미널 출력이 판단 사이클 5단계(초기 지갑 상태 → x402 추론 결제 → 판단 결과 →
PolicyGuard 판정 → 집행 인텐트/트랜잭션)를 순서대로 보여준다. `--emit-json` 산출물이
프론트엔드가 그대로 읽는 스냅샷이다 — 둘은 같은 `Snapshot` 객체에서 나오므로 화면과
터미널 출력이 어긋나지 않는다.

---

## 테스트 · 린트 · 타입체크

모두 `backend/` 에서 가상환경을 활성화한 뒤 실행한다. 아래는 이 저장소에서 **직접 실행한
결과**다 (외부 네트워크·자격증명 불필요).

```bash
cd backend
source venv/bin/activate

pytest -q
# → 154 passed

ruff check .
# → 103 errors (95 fixable with `--fix`) — 대부분 스타일 규칙, 신규 수정 사항은 아님

mypy
# → Found 1 error: providers/aistudio.py 의 `from google.genai import Client` 가
#    import-untyped 로 걸린다 (google-genai 미설치 — 위 "AI Studio 경로" 설치 안내 참고)
```

`backend/mypy.ini` 는 `treagent/` 판단 엔진과 그 테스트만 strict 로 본다.
`chain/` · `mock_data.py` · `services/` 는 `ignore_errors = True` 로 범위 밖이다
(1~3단계 float 규약의 기존 코드, 이번 판단 엔진 세션의 범위가 아니라는 주석 참고).

프론트엔드:

```bash
cd frontend
npm run build   # tsc --noEmit && vite build — 통과 확인
npm run lint    # eslint . — 통과 확인 (경고/에러 없음)
```

---

## 네트워크 분리 — Devnet ≠ pay.sh Sandbox

TreAgent 는 **서로 다른 두 네트워크**를 동시에 쓰고, 절대 하나로 합쳐 보여주지 않는다
(`backend/scripts/demo.py`, `backend/treagent/judgment/providers/paysh.py` docstring 근거):

- **자금 집행 레이어 — Solana Devnet.** Vault/Payroll/Operations/Vendor 지갑, 실제
  온체인 트랜잭션, `https://explorer.solana.com/tx/<sig>?cluster=devnet` 로 조회 가능.
- **결제 프로토콜 레이어 — pay.sh Sandbox (Surfpool localnet).** Gemini 추론 요청의
  x402 결제 전용. **공개 Explorer 에서 조회 불가능** — localnet 서명에는 절대
  `explorer_url` 을 만들지 않는다 (`signature_verifiable=False` 로 항상 고정).

`pay` CLI 에는 `--devnet` 모드가 없다 (`--sandbox` / `--local` / `--mainnet` 뿐이고,
대회 규정상 `mainnet` 은 코드 경로 자체를 두지 않았다). 따라서 x402 추론 비용은
**Devnet Operations Wallet 잔고에서 실제로 차감되지 않는다** — sandbox 지갑에서만
빠져나간다. 대신 그 비용은 `API_USAGE` obligation 으로 "기표"되어 다음 판단 사이클의
Operations Wallet 부족분 예측에 반영된다 (`treagent/judgment/inference_cost.py`) — 이
재귀 루프가 두 레이어를 잇는 유일한 연결점이다.

---

## 파일 구조

```
TreAgent/
├── .env.example              환경변수 템플릿
├── backend/
│   ├── main.py                FastAPI 앱, CORS, 라우터 등록, /health
│   ├── config.py               settings.MOCK_MODE, GEMINI_API_KEY
│   ├── mock_data.py             인메모리 상태 (A 경로 전용)
│   ├── requirements.txt
│   ├── routers/                 dashboard/wallets/payments/policy/agent/execution/logs
│   ├── chain/                    devnet RPC 클라이언트, 지갑, 잔고조회/SPL 전송 (실제 구현)
│   │   ├── swap.py                 Jupiter 스왑 — 스텁 (4단계)
│   │   └── pay.py                  Solana Pay 배치결제 — 스텁 (4단계)
│   ├── services/                 라우터 ↔ chain/mock_data 접합부
│   │   ├── solana_client.py        MOCK_MODE 분기 (실제 구현)
│   │   ├── decision.py             Gemini 판단 — 스텁 (3단계)
│   │   ├── policy.py               정책 검증 — 스텁 (3단계)
│   │   └── executor.py             실행 오케스트레이터 — 스텁 (3단계)
│   ├── treagent/judgment/         B 경로: 판단 엔진 (실제 구현, pytest 로 검증)
│   │   ├── forecaster.py            결정론적 부족분 계산
│   │   ├── gating.py                호출 여부 게이팅
│   │   ├── guard.py                 PolicyGuard 10단계 검사
│   │   ├── engine.py                JudgmentEngine 오케스트레이터
│   │   ├── inference_cost.py        x402 비용 → obligation 기표
│   │   └── providers/               mock.py / aistudio.py / paysh.py
│   ├── gateway/paywall.yml      자체 x402 게이트웨이 스펙 (pay.sh sandbox 용)
│   ├── scripts/
│   │   ├── demo.py                B 경로 데모 CLI + --emit-json
│   │   └── check_balances.py      devnet 지갑 상태 표 + 전송 테스트 CLI
│   └── tests/                   pytest 154개 (전부 treagent/judgment 대상)
└── frontend/
    ├── public/snapshot.*.json   커밋된 시나리오 스냅샷 (데모용)
    └── src/
        ├── lib/useSnapshot.ts    유일한 데이터 소스 — 정적 JSON fetch
        └── lib/derive.ts          백엔드 스냅샷 어휘 → UI 어휘 정규화
```

백엔드 엔드포인트 전체 목록, 상태 코드 규칙, `chain/` 온체인 불변 규칙은
[backend/README.md](backend/README.md)에 더 자세히 있다.

---

## 이 README 작성 시 실행 검증한 것 / 하지 않은 것

| 명령 | 실행 여부 | 결과 |
| --- | --- | --- |
| `python -m scripts.demo --scenario {normal_shortfall,prompt_injection,inference_budget_low} --provider mock` | ✅ 실행함 | 3개 시나리오 모두 정상 출력 |
| `python -m scripts.demo --scenario inference_budget_low --provider mock --emit-json <path>` | ✅ 실행함 | JSON 스냅샷 정상 생성 |
| `python -m scripts.demo --help`, `python scripts/check_balances.py --help` | ✅ 실행함 | 위 문서의 옵션 표는 이 출력 그대로 |
| `pytest -q` | ✅ 실행함 | 154 passed |
| `ruff check .` | ✅ 실행함 | 103 errors (95 auto-fixable) — 수정하지 않고 있는 그대로 보고 |
| `mypy` | ✅ 실행함 | 1 error (`google.genai` import-untyped) |
| `npm run build` (`tsc --noEmit && vite build`) | ✅ 실행함 | 통과 |
| `npm run lint` (`eslint .`) | ✅ 실행함 | 통과, 경고 없음 |
| `import google.genai`, `pip show google-genai` | ✅ 실행함 | 미설치 확인 (`ModuleNotFoundError`) |
| `pay --version`, `pay --help`, `pay setup --help`, `pay gate api --help` | ✅ 실행함 (로컬 CLI 도움말, 네트워크·결제 없음) | 버전 0.27.0, 옵션은 위 문서에 반영 |
| `python -m scripts.demo --provider aistudio` (실 Gemini 호출) | ❌ 실행 안 함 | `GEMINI_API_KEY` + 외부 API 호출 필요, 승인 필요 |
| `pay setup`, `pay --sandbox gate api ...` (실 계정 생성·게이트웨이 기동) | ❌ 실행 안 함 | 계정 생성·자금 이동 가능성, 승인 필요 |
| `python -m chain.wallet`, `solana airdrop`, `spl-token create-token`, `check_balances.py --from ... --to ...` (devnet 실전송) | ❌ 실행 안 함 | 실제 devnet 키·트랜잭션 생성, 승인 필요 |
| `uvicorn main:app --reload` (서버 상시 기동) | ❌ 실행 안 함 | 소스 코드 검토로 엔드포인트·CORS 확인 |
