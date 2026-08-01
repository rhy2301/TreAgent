# TreAgent Backend

Solana 온체인 트레저리의 **예정 지출을 AI가 예측**하고, 정책 가드레일 안에서
**Solana Pay(x402) 결제까지 자율 실행**하는 트레저리 에이전트의 API 서버.

> **현재 단계 (1단계)**: 모든 응답은 `mock_data.py` 의 인메모리 하드코딩 데이터로 반환된다.
> Solana SDK / Gemini 연동은 3단계에서 `services/` 스텁을 채우며 붙인다.

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

상태는 프로세스 메모리에만 존재하므로 **서버를 재시작하면 데모 시나리오가 초기화**된다.

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
(현재 단계에서는 실제 온체인 트랜잭션이 아니다.)

---

## 파일 구조

```
backend/
├── main.py                  FastAPI 앱, CORS, 라우터 등록, /health
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
└── services/                ← 3단계에서 구현할 스텁 (시그니처 + docstring 만)
    ├── solana_client.py     get_balances, transfer_usdc, get_transaction, request_airdrop
    ├── decision.py          analyze, build_prompt, parse_response
    ├── policy.py            validate, validate_batch, is_whitelisted
    └── executor.py          execute, execute_action, swap_and_transfer, pay_batch
```

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
