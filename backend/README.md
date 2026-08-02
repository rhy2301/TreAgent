# TreAgent Backend

Solana 온체인 트레저리의 **예정 지출을 AI가 예측**하고, 정책 가드레일 안에서
**Solana Pay(x402) 결제까지 자율 실행**하는 트레저리 에이전트의 API 서버.

> **현재 단계 (3단계)**: 잔고 조회와 USDC 전송은 Solana devnet 에 실제로 연결된다
> (`chain/`). 판단(Gemini)·스왑·배치 결제는 아직 mock 이다.
> `MOCK_MODE=true` 로 두면 온체인 호출 없이 전부 `mock_data.py` 로 응답한다.

---

## 실행

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --reload
```

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

CORS 는 `http://localhost:3000`, `http://localhost:5173` 을 허용한다.

mock 상태는 프로세스 메모리에만 존재하므로 **서버를 재시작하면 데모 시나리오가 초기화**된다.
온체인 잔고는 당연히 재시작과 무관하다.

---

## devnet 준비

`.env.example` 을 `.env` 로 복사한 뒤 아래 순서로 채운다.

### 1. 지갑 4개 생성

```bash
cd backend
python -m chain.wallet          # VAULT_/OPERATIONS_/PAYROLL_/VENDOR_ 키 출력
```

출력된 줄을 `.env` 에 붙여넣는다. **지갑마다 서로 다른 키**여야 한다.
같은 키를 두 지갑에 넣으면 서버 startup 이 경고를 남기고, `vault → payroll`
이체가 자기 자신에게 보내는 무의미한 트랜잭션이 된다.

### 2. 가스비용 SOL 확보

```bash
solana airdrop 2 <PUBLIC_KEY> --url devnet     # 또는 https://faucet.solana.com
```

지갑당 최소 `0.01 SOL` 이 있어야 한다 (수수료 + 수신자 ATA rent).
공용 faucet 은 rate limit 이 잦으므로 웹 faucet 을 함께 쓰는 편이 빠르다.

### 3. USDC 역할을 할 SPL 토큰 발행

devnet 에는 우리가 마음대로 쓸 수 있는 USDC 가 없으므로 직접 만든다.
**`--decimals 6` 이어야 한다.** `transfer_checked` 가 decimals 를 검증하기 때문에
다른 값으로 만들면 전송이 런타임에서 거부된다.

```bash
spl-token create-token --decimals 6 --url devnet     # 출력된 mint 주소를 USDC_MINT 에
spl-token create-account <MINT> --url devnet
spl-token mint <MINT> 1000000 --url devnet           # vault 에 1,000,000 지급
```

### 4. 연동 확인

```bash
python scripts/check_balances.py
```

지갑 4개의 주소·SOL·USDC·ATA 존재 여부가 표로 나온다.
ATA 가 `NO` 인 것은 정상이다 — 그 지갑으로 첫 전송을 할 때 같은 트랜잭션에서 자동 생성된다.

```bash
# 전송 테스트 — 반드시 --dry-run 을 먼저
python scripts/check_balances.py --from vault --to payroll --amount 10 --dry-run
python scripts/check_balances.py --from vault --to payroll --amount 10
```

---

## 온체인 레이어 규칙

`chain/` 바깥에서는 **온체인 raw 정수를 절대 보지 않는다.** API·서비스 레이어의
금액은 전부 float USDC 이고, 경계에서 `to_raw()` / `to_ui()` 를 통과한다.
`to_raw` 가 `round()` 를 쓰는 것은 장식이 아니다 — `int(2.01 * 10**6)` 은
부동소수 오차로 `2009999` 가 되어 금액이 깎인다.

그 밖의 불변 규칙:

- SPL 전송은 `transfer_checked` 만 쓴다. 일반 `transfer` 는 decimals 를 검증하지 않는다.
- 잔고 조회에서 ATA 가 없으면 예외가 아니라 `0.0` 이다.
- 수신자 ATA 가 없으면 `create_idempotent_associated_token_account` 를 **같은
  트랜잭션 앞에** 붙인다. 별도 트랜잭션으로 나누면 생성만 성공한 중간 상태가 생긴다.
- 확정 실패로 재시도하기 전에 항상 기존 서명 상태를 먼저 조회한다. 무조건 재전송하면
  같은 금액이 두 번 나갈 수 있다.
- 프라이빗 키는 로그·응답에 넣지 않는다. `solders.Keypair.__repr__` 은 signing key
  를 그대로 출력하므로 `Keypair` 객체를 f-string 에 넣지 말 것.

### 예외 → HTTP 상태

라우터에는 `try/except` 를 두지 않는다. `chain.exceptions.SolanaError` 를 그대로
올려보내면 `main.py` 의 핸들러가 변환한다.

| 예외 | 상태 | 상황 |
| --- | --- | --- |
| `InsufficientBalance` | 409 | USDC 잔고 부족 (필요액/보유액 포함) |
| `InsufficientSol` | 409 | 수수료 + ATA rent 용 SOL 부족 |
| `InvalidAddress` / `InvalidAmount` | 422 | 주소 파싱 실패, 금액 0 이하 또는 소수점 6자리 초과 |
| `TransactionFailed` | 502 | 온체인 실패 또는 재시도 후 확정 실패 (제출된 서명 포함) |
| `RpcUnavailable` / `ConfigError` | 503 | RPC 장애·레이트리밋, 환경변수 누락 |

---

## 엔드포인트

| Method | Path | Tag | 설명 |
| --- | --- | --- | --- |
| GET | `/health` | health | 서버 상태, 버전, 클러스터 |
| GET | `/api/dashboard/summary` | dashboard | 총 USDC, 72h 예정 지출, at_risk 건수, 위험도, 헤드라인, 사이클 시각, 일일 사용/한도 |
| GET | `/api/wallets` | wallets | 지갑 4개 목록 |
| GET | `/api/wallets/{wallet_id}` | wallets | 지갑 상세 + `recent_transactions[]`. 없으면 404 |
| GET | `/api/payments/upcoming` | payments | `hours=72`, `wallet_id?`, `status?` → `due_at` 오름차순 |
| GET | `/api/payments/{payment_id}` | payments | `recipients` 전체 포함. 없으면 404 |
| POST | `/api/payments` | payments | 201. 예정 지출 수동 추가 (데모용) |
| GET | `/api/policy` | policy | 현재 정책 |
| PUT | `/api/policy` | policy | 부분 업데이트. `daily_used` 는 무시. `max_single_transfer > max_daily_total` 이면 422 |
| GET | `/api/policy/whitelist` | policy | `[{address, label, added_at}]` |
| POST | `/api/agent/analyze` | agent | `{horizon_hours=72, force_refresh=false}` → `Decision`. **자금 이동 없음** |
| POST | `/api/agent/execute` | agent | `{decision_id, action_indices?, approved_by?, dry_run=false}` → `ExecutionResult` |
| POST | `/api/agent/run-cycle` | agent | 잔고 갱신 → analyze → `auto_execute` 면 execute |
| GET | `/api/agent/status` | agent | `running`, `interval_minutes=15`, 사이클/실행 횟수 |
| POST | `/api/swap/quote` | execution | `{input_token, output_token, amount}` → Jupiter 견적 + 슬리피지 정책 판정 |
| POST | `/api/pay` | execution | `{payment_id, dry_run}` → Solana Pay(x402) 배치 송금. 잔고 부족이면 409 |
| GET | `/api/logs` | logs | `limit=50`, `offset=0`, `type?` → 최신순 |
| GET | `/api/alerts` | logs | 경보 목록 (최신순) |

### 상태 코드 규칙

| 상황 | 응답 |
| --- | --- |
| 없는 지갑 / 지출 / 판단 | `404` |
| `max_single_transfer > max_daily_total` | `422` |
| `action_indices` 범위 초과 | `422` |
| 이미 실행된 `decision` 재실행 | `409` |
| `/api/pay` 출금 지갑 잔고 부족 | `409` |
| **정책 위반으로 실행 차단** | `200` + `status="blocked"` + `blocked_reason` |

정책 위반은 에러가 아니라 **정상적인 판단 결과**이므로 200 으로 내려간다.
운영자가 정책을 완화하거나 액션을 분할해 다시 시도하는 흐름을 그대로 표현하기 위함이다.

---

## 판단과 실행의 분리

`analyze` 와 `execute` 는 반드시 별도 엔드포인트다.

```
POST /api/agent/analyze   → Decision 생성. 온체인 상태를 읽기만 한다.
POST /api/agent/execute   → 그 Decision 을 정책 재검증 후 실행한다.
```

`execute` 는 Decision 에 저장된 `policy_check` 를 신뢰하지 않고 **현재 정책으로 다시 검증한다.**
판단 시점과 실행 시점 사이에 잔고나 정책이 바뀌었을 수 있기 때문이다.

`blocked` / `simulated` 실행은 자금이 움직이지 않았으므로 재실행을 허용한다.
409 는 `success` / `partial` 로 이미 확정된 Decision 에만 발생한다.

---

## 데모 시나리오

서버를 새로 띄우면 아래 상태에서 시작한다.

| 지갑 | USDC | SOL | min_buffer |
| --- | ---: | ---: | ---: |
| `vault` (Treasury Vault) | 120,000 | 45.2 | 10,000 |
| `operations` (Operations) | 8,500 | 2.1 | 1,000 |
| `payroll` (Payroll) | **5,000** | 0.5 | 2,000 |
| `vendor` (Vendor Settlement) | 12,000 | 1.0 | 1,000 |

| 예정 지출 | 지갑 | 금액 | 수신자 | 상태 |
| --- | --- | ---: | ---: | --- |
| `pay_001` 8월 1주차 기여자 급여 | payroll | 28,000 | 14명 | `at_risk` |
| `pay_002` Vendor 정산 - Design Agency | vendor | 6,500 | 1명 | `scheduled` |
| `pay_003` OpenAI API 이용료 | operations | 1,200 | 1명 | `scheduled` |

정책: 1회 10,000 / 일일 50,000 (사용 12,000) / 슬리피지 50bps / `auto_execute=false`

### 데모 흐름

```bash
# 1. 위험 감지 — payroll 이 28,000 USDC 급여를 지급할 수 없다
curl localhost:8000/api/dashboard/summary

# 2. AI 판단 — vault → payroll 25,000 USDC (부족분 23,000 + 버퍼 2,000)
curl -X POST localhost:8000/api/agent/analyze \
  -H 'Content-Type: application/json' -d '{"horizon_hours":72}'

# 3. 실행 시도 → 1회 한도 10,000 초과로 차단 (200 + status="blocked")
curl -X POST localhost:8000/api/agent/execute \
  -H 'Content-Type: application/json' -d '{"decision_id":"dec_001"}'

# 4. 운영자가 한도 상향
curl -X PUT localhost:8000/api/policy \
  -H 'Content-Type: application/json' -d '{"max_single_transfer":30000}'

# 5. 재실행 → 성공. payroll 30,000 / vault 95,000, pay_001 은 funded 로 승격
curl -X POST localhost:8000/api/agent/execute \
  -H 'Content-Type: application/json' -d '{"decision_id":"dec_001","approved_by":"ops"}'

# 6. Solana Pay(x402) 로 14명에게 배치 송금
curl -X POST localhost:8000/api/pay \
  -H 'Content-Type: application/json' -d '{"payment_id":"pay_001"}'

# 7. 감사 로그 확인
curl 'localhost:8000/api/logs?limit=10'
```

트랜잭션 서명은 88자 base58 문자열이며, explorer 링크는
`https://explorer.solana.com/tx/{signature}?cluster=devnet` 형식으로 생성된다.
위 시나리오의 판단·스왑·배치 결제는 아직 mock 이지만, `services.solana_client` 를
거치는 잔고 조회와 USDC 이체는 `MOCK_MODE=false` 일 때 실제 devnet 트랜잭션이다.

---

## 파일 구조

```
backend/
├── main.py                  FastAPI 앱, CORS, 라우터 등록, /health, 예외 핸들러, lifespan
├── config.py                settings.MOCK_MODE 등 운영 스위치
├── schemas.py               Pydantic v2 모델 전부
├── mock_data.py             인메모리 상태 + 조회/변경 헬퍼
├── requirements.txt
├── routers/
│   ├── dashboard.py         /api/dashboard/summary
│   ├── wallets.py           /api/wallets
│   ├── payments.py          /api/payments
│   ├── policy.py            /api/policy
│   ├── agent.py             /api/agent  (analyze / execute / run-cycle / status)
│   ├── execution.py         /api/swap/quote, /api/pay
│   └── logs.py              /api/logs, /api/alerts
├── chain/                   ← 온체인 레이어 (3단계에서 구현)
│   ├── client.py            AsyncClient 싱글턴, 상수, to_raw/to_ui, explorer URL
│   ├── exceptions.py        SolanaError 계열 도메인 예외
│   ├── wallet.py            지갑 레지스트리, load_keypair, validate_env
│   ├── transfer.py          잔고 조회 + SPL USDC 전송 (핵심)
│   ├── swap.py              Jupiter 스왑 (4단계 스텁)
│   └── pay.py               Solana Pay 배치 결제 (4단계 스텁)
├── scripts/
│   └── check_balances.py    지갑 상태 표 + 전송 테스트 CLI
└── services/
    ├── solana_client.py     chain/ 과 라우터 사이의 접합부 (MOCK_MODE 분기)
    ├── decision.py          analyze, build_prompt, parse_response          (스텁)
    ├── policy.py            validate, validate_batch, is_whitelisted       (스텁)
    └── executor.py          execute, execute_action, swap_and_transfer     (스텁)
```

### `chain/` 이라는 이름에 대해

설계서에는 `backend/solana/` 로 적혀 있지만 그 이름은 쓸 수 없다.
`backend/` 가 sys.path 에 올라가 있어서 solana-py 라이브러리의 최상위 `solana`
패키지와 정면으로 충돌하기 때문이다.

| | 결과 |
| --- | --- |
| `backend/solana/__init__.py` 있음 | 라이브러리가 가려져 `from solana.rpc.async_api import ...` 실패 |
| `__init__.py` 없음 | site-packages 의 정규 패키지가 이겨서 `backend/solana/` 를 import 불가 |

두 경우 모두 치명적이라 `chain/` 으로 분리했다. 이름을 되돌리려면 `backend/` 자체를
패키지로 만들고 main.py 와 라우터 7개를 `from backend.xxx import` 로 바꿔야 한다.

### 상태 접근 규칙

라우터는 `mock_data` 의 전역 변수를 직접 건드리지 않고 헬퍼 함수만 호출한다.

| 헬퍼 | 용도 |
| --- | --- |
| `get_wallet(id)` / `list_wallets()` / `adjust_balance(id, delta)` | 지갑 |
| `get_payment(id)` / `list_payments(hours, wallet_id, status)` / `add_payment(...)` | 예정 지출 |
| `get_policy()` / `update_policy(patch)` / `validate_actions(actions)` | 정책 |
| `build_decision(horizon_hours)` / `get_decision(id)` | 판단 |
| `add_execution(...)` / `find_committed_execution(decision_id)` / `make_tx(...)` | 실행 |
| `add_log(...)` / `list_logs(...)` / `add_alert(...)` / `list_alerts()` | 로그·경보 |

3단계에서 `mock_data.build_decision` → `services.decision.analyze`,
`mock_data.validate_actions` → `services.policy.validate` 로 교체하면
라우터는 그대로 두고 백엔드만 실제 온체인으로 전환된다.
