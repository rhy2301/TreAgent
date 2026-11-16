"""인메모리 mock 상태 + 조회/변경 헬퍼.

DB 없이 모듈 레벨 변수만으로 데모 시나리오를 유지한다.
라우터는 절대 아래 전역 변수를 직접 건드리지 않고 이 파일의 헬퍼만 호출한다.

데모 스토리
-----------
payroll 지갑에 5,000 USDC 밖에 없는데 금요일에 28,000 USDC 급여 배치가 예정되어 있다.
에이전트는 vault -> payroll 25,000 USDC 이체를 제안하지만, 1회 이체 한도(10,000)에
걸려 실행이 차단된다. 운영자가 정책을 완화하면 그때 실행이 통과한다.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

# --------------------------------------------------------------------------- #
# 기본값 / 유틸
# --------------------------------------------------------------------------- #

APP_VERSION = "0.1.0"
CLUSTER = "devnet"
EXPLORER_TX_URL = "https://explorer.solana.com/tx/{signature}?cluster=devnet"
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# 모듈이 import 되는 시점을 "지금"으로 잡아 데모 데이터가 항상 72시간 창 안에 들어오게 한다.
BOOT_TIME = datetime.now(UTC)

# 시드 데이터의 서명과 겹치지 않도록 고정 시드를 쓰지 않는다.
_rng = random.Random()
_counters: dict[str, int] = {}


def utcnow() -> datetime:
    """모든 타임스탬프의 단일 소스."""
    return datetime.now(UTC)


def next_id(prefix: str) -> str:
    """`dec_001` 형태의 순번 id 를 만든다."""
    _counters[prefix] = _counters.get(prefix, 0) + 1
    return f"{prefix}_{_counters[prefix]:03d}"


def make_signature() -> str:
    """그럴듯한 88자 base58 트랜잭션 서명을 생성한다."""
    return "".join(_rng.choice(_BASE58_ALPHABET) for _ in range(88))


def explorer_url(signature: str) -> str:
    return EXPLORER_TX_URL.format(signature=signature)


# --------------------------------------------------------------------------- #
# 지갑
# --------------------------------------------------------------------------- #

WALLETS: list[dict[str, Any]] = [
    {
        "id": "vault",
        "name": "Treasury Vault",
        "type": "vault",
        "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        "usdc_balance": 120_000.0,
        "sol_balance": 45.2,
        "min_buffer": 10_000.0,
        "updated_at": BOOT_TIME - timedelta(minutes=3),
    },
    {
        "id": "operations",
        "name": "Operations",
        "type": "operations",
        "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        "usdc_balance": 8_500.0,
        "sol_balance": 2.1,
        "min_buffer": 1_000.0,
        "updated_at": BOOT_TIME - timedelta(minutes=3),
    },
    {
        "id": "payroll",
        "name": "Payroll",
        "type": "payroll",
        "address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "usdc_balance": 5_000.0,
        "sol_balance": 0.5,
        "min_buffer": 2_000.0,
        "updated_at": BOOT_TIME - timedelta(minutes=3),
    },
    {
        "id": "vendor",
        "name": "Vendor Settlement",
        "type": "vendor",
        "address": "3nJ8kQpVUyTgHUEsTLYm6BEbmvXAtnkGdCbszDPUrMSp",
        "usdc_balance": 12_000.0,
        "sol_balance": 1.0,
        "min_buffer": 1_000.0,
        "updated_at": BOOT_TIME - timedelta(minutes=3),
    },
]


def list_wallets() -> list[dict[str, Any]]:
    return WALLETS


def get_wallet(wallet_id: str) -> dict[str, Any] | None:
    """지갑 id 로 조회. 없으면 None."""
    return next((w for w in WALLETS if w["id"] == wallet_id), None)


def get_wallet_by_address(address: str) -> dict[str, Any] | None:
    return next((w for w in WALLETS if w["address"] == address), None)


def total_usdc() -> float:
    return sum(w["usdc_balance"] for w in WALLETS)


def adjust_balance(wallet_id: str, usdc_delta: float) -> dict[str, Any] | None:
    """지갑 USDC 잔고를 증감시키고 updated_at 을 갱신한다."""
    wallet = get_wallet(wallet_id)
    if wallet is None:
        return None
    wallet["usdc_balance"] = round(wallet["usdc_balance"] + usdc_delta, 2)
    wallet["updated_at"] = utcnow()
    return wallet


def refresh_balances() -> list[dict[str, Any]]:
    """온체인 잔고 재조회를 흉내낸다 (3단계에서 solana_client 로 대체)."""
    now = utcnow()
    for wallet in WALLETS:
        wallet["updated_at"] = now
    return WALLETS


# --------------------------------------------------------------------------- #
# 예정 지출
# --------------------------------------------------------------------------- #

_PAYROLL_RECIPIENTS: list[dict[str, Any]] = [
    {"name": "김지훈", "address": "8FE27ioQh3T7o22QsYVT5Re8NnHFqmFNbdqwiF3ywuZQ", "amount": 2_400.0},
    {"name": "이서연", "address": "Ap2dNrEpVpTPEcQTumHkFxdgMD5MJd7cvfHTbwtvHYYE", "amount": 2_200.0},
    {"name": "박민우", "address": "6dsrKcqYVBQ1p6WuVBHhbaJDDTHkC5DwLtEZmuCyEgvS", "amount": 2_100.0},
    {"name": "최유진", "address": "GsbwXfJraMomNxBcjK7xK2xQx5MQgQx2TjHnpXsMdshC", "amount": 2_000.0},
    {"name": "정하늘", "address": "4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi", "amount": 2_000.0},
    {"name": "강도현", "address": "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH", "amount": 2_000.0},
    {"name": "윤채원", "address": "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S", "amount": 2_000.0},
    {"name": "임태영", "address": "CjUCk1BLcVXJXqEQPkBUS8GsRXmxdSaXjmzTfGDLL2sX", "amount": 2_000.0},
    {"name": "한소미", "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "amount": 1_900.0},
    {"name": "오준호", "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "amount": 1_900.0},
    {"name": "서예린", "address": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "amount": 1_900.0},
    {"name": "노건우", "address": "So11111111111111111111111111111111111111112", "amount": 1_900.0},
    {"name": "배수아", "address": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", "amount": 1_850.0},
    {"name": "문재현", "address": "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj", "amount": 1_850.0},
]

PAYMENTS: list[dict[str, Any]] = [
    {
        "id": "pay_001",
        "title": "8월 1주차 기여자 급여",
        "type": "payroll",
        "amount": 28_000.0,
        "recipient_count": 14,
        "recipients": _PAYROLL_RECIPIENTS,
        "source_wallet": "payroll",
        # 금요일 정산 — 데모에서 항상 72시간 창 안쪽에 위치
        "due_at": BOOT_TIME + timedelta(hours=46),
        "status": "at_risk",
    },
    {
        "id": "pay_002",
        "title": "Vendor 정산 - Design Agency",
        "type": "vendor",
        "amount": 6_500.0,
        "recipient_count": 1,
        "recipients": [
            {
                "name": "Design Agency Co.",
                "address": "BXXkv6z8ykpG1yuvUDPgh732wzVHB69RnB9YgSYh3itW",
                "amount": 6_500.0,
            }
        ],
        "source_wallet": "vendor",
        "due_at": BOOT_TIME + timedelta(hours=60),
        "status": "scheduled",
    },
    {
        "id": "pay_003",
        "title": "OpenAI API 이용료",
        "type": "api_fee",
        "amount": 1_200.0,
        "recipient_count": 1,
        "recipients": [
            {
                "name": "OpenAI",
                "address": "AWjyDsPBqBGSWFmDkPYcvbLXGrRHvHvVvhxLxWCcVaB6",
                "amount": 1_200.0,
            }
        ],
        "source_wallet": "operations",
        "due_at": BOOT_TIME + timedelta(hours=20),
        "status": "scheduled",
    },
]


def list_payments(
    hours: int = 72,
    wallet_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """`hours` 시간 내 예정 지출을 due_at 오름차순으로 반환한다."""
    horizon = utcnow() + timedelta(hours=hours)
    items = [p for p in PAYMENTS if p["due_at"] <= horizon]
    if wallet_id is not None:
        items = [p for p in items if p["source_wallet"] == wallet_id]
    if status is not None:
        items = [p for p in items if p["status"] == status]
    return sorted(items, key=lambda p: p["due_at"])


def get_payment(payment_id: str) -> dict[str, Any] | None:
    return next((p for p in PAYMENTS if p["id"] == payment_id), None)


def add_payment(payment: dict[str, Any]) -> dict[str, Any]:
    """예정 지출을 추가하고 id 를 부여한다."""
    payment = dict(payment)
    payment["id"] = next_id("pay_manual")
    PAYMENTS.append(payment)
    return payment


def set_payment_status(payment_id: str, status: str) -> dict[str, Any] | None:
    payment = get_payment(payment_id)
    if payment is None:
        return None
    payment["status"] = status
    return payment


def upcoming_total(hours: int = 72) -> float:
    """미결제(paid 제외) 예정 지출 합계."""
    return sum(p["amount"] for p in list_payments(hours) if p["status"] != "paid")


def at_risk_payments(hours: int = 72) -> list[dict[str, Any]]:
    """출금 지갑 잔고가 예정 금액 + 최소 버퍼에 못 미치는 건들."""
    risky: list[dict[str, Any]] = []
    for payment in list_payments(hours):
        if payment["status"] == "paid":
            continue
        wallet = get_wallet(payment["source_wallet"])
        if wallet is None:
            continue
        if wallet["usdc_balance"] < payment["amount"] + wallet["min_buffer"]:
            risky.append(payment)
    return risky


# --------------------------------------------------------------------------- #
# 정책
# --------------------------------------------------------------------------- #

WHITELIST: list[dict[str, Any]] = [
    {
        "address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "label": "Payroll Wallet (internal)",
        "added_at": BOOT_TIME - timedelta(days=42),
    },
    {
        "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        "label": "Operations Wallet (internal)",
        "added_at": BOOT_TIME - timedelta(days=42),
    },
    {
        "address": "3nJ8kQpVUyTgHUEsTLYm6BEbmvXAtnkGdCbszDPUrMSp",
        "label": "Vendor Settlement Wallet (internal)",
        "added_at": BOOT_TIME - timedelta(days=42),
    },
    {
        "address": "BXXkv6z8ykpG1yuvUDPgh732wzVHB69RnB9YgSYh3itW",
        "label": "Design Agency Co.",
        "added_at": BOOT_TIME - timedelta(days=18),
    },
    {
        "address": "AWjyDsPBqBGSWFmDkPYcvbLXGrRHvHvVvhxLxWCcVaB6",
        "label": "OpenAI (API fee)",
        "added_at": BOOT_TIME - timedelta(days=11),
    },
]

POLICY: dict[str, Any] = {
    "max_single_transfer": 10_000.0,
    "max_daily_total": 50_000.0,
    "max_slippage_bps": 50,
    "min_buffers": {
        "vault": 10_000.0,
        "operations": 1_000.0,
        "payroll": 2_000.0,
        "vendor": 1_000.0,
    },
    "whitelist": [entry["address"] for entry in WHITELIST],
    "auto_execute": False,
    "daily_used": 12_000.0,
}


def get_policy() -> dict[str, Any]:
    return POLICY


def get_whitelist() -> list[dict[str, Any]]:
    return WHITELIST


def update_policy(patch: dict[str, Any]) -> dict[str, Any]:
    """정책 부분 업데이트. daily_used 는 무시한다."""
    for key, value in patch.items():
        if key == "daily_used" or value is None:
            continue
        POLICY[key] = value
    return POLICY


def consume_daily(amount: float) -> float:
    """일일 사용액을 누적한다."""
    POLICY["daily_used"] = round(POLICY["daily_used"] + amount, 2)
    return POLICY["daily_used"]


def validate_action(action: dict[str, Any], policy: dict[str, Any] | None = None) -> list[str]:
    """단일 액션에 대한 정책 위반 목록을 반환한다. 빈 리스트면 통과."""
    policy = policy or POLICY
    violations: list[str] = []

    amount = action.get("amount") or 0.0
    if action["type"] in ("wait", "alert"):
        return violations

    if amount > policy["max_single_transfer"]:
        violations.append(
            f"1회 이체 한도 초과: {amount:,.0f} > {policy['max_single_transfer']:,.0f} USDC"
        )

    if policy["daily_used"] + amount > policy["max_daily_total"]:
        violations.append(
            f"일일 누적 한도 초과: {policy['daily_used'] + amount:,.0f} > "
            f"{policy['max_daily_total']:,.0f} USDC"
        )

    to_wallet = action.get("to_wallet")
    if to_wallet:
        target = get_wallet(to_wallet)
        if target is not None and target["address"] not in policy["whitelist"]:
            violations.append(f"화이트리스트 미등록 주소: {target['address']}")

    from_wallet = action.get("from_wallet")
    if from_wallet:
        source = get_wallet(from_wallet)
        if source is None:
            violations.append(f"알 수 없는 출금 지갑: {from_wallet}")
        else:
            buffer = policy["min_buffers"].get(from_wallet, source["min_buffer"])
            if source["usdc_balance"] - amount < buffer:
                violations.append(
                    f"{source['name']} 최소 버퍼 침해: 이체 후 잔고 "
                    f"{source['usdc_balance'] - amount:,.0f} < {buffer:,.0f} USDC"
                )

    return violations


def validate_actions(
    actions: Iterable[dict[str, Any]], policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    """액션 묶음 전체에 대한 PolicyCheck 딕셔너리를 만든다."""
    violations: list[str] = []
    for action in actions:
        violations.extend(validate_action(action, policy))
    return {
        "passed": not violations,
        "violations": violations,
        "requires_approval": bool(violations) or not (policy or POLICY)["auto_execute"],
    }


# --------------------------------------------------------------------------- #
# 판단 (Decision)
# --------------------------------------------------------------------------- #

DECISIONS: list[dict[str, Any]] = []


def build_decision(horizon_hours: int = 72) -> dict[str, Any]:
    """Gemini 판단 결과를 흉내낸 Decision 을 만들어 저장한다.

    3단계에서 services.decision.analyze() 호출로 대체된다.
    """
    now = utcnow()
    risky = at_risk_payments(horizon_hours)

    actions: list[dict[str, Any]] = []
    for payment in risky:
        wallet = get_wallet(payment["source_wallet"])
        if wallet is None:
            continue
        buffer = POLICY["min_buffers"].get(wallet["id"], wallet["min_buffer"])
        shortfall = payment["amount"] - wallet["usdc_balance"]
        amount = round(max(shortfall, 0) + buffer, 2)
        actions.append(
            {
                "type": "transfer",
                "from_wallet": "vault",
                "to_wallet": wallet["id"],
                "amount": amount,
                "token": "USDC",
                "execute_by": payment["due_at"] - timedelta(hours=12),
                "reason": (
                    f"{payment['title']}({payment['amount']:,.0f} USDC) 지급을 위해 "
                    f"{wallet['name']} 지갑에 부족분 {max(shortfall, 0):,.0f} USDC 와 "
                    f"최소 버퍼 {buffer:,.0f} USDC 를 합쳐 충전한다."
                ),
            }
        )

    check = validate_actions(actions)

    if risky:
        # 실제 Gemini 출력처럼 보이도록 근거 문장을 현재 잔고/금액에서 직접 만든다.
        head = risky[0]
        head_wallet = get_wallet(head["source_wallet"])
        assert head_wallet is not None
        head_action = actions[0]
        vault = get_wallet("vault")
        assert vault is not None
        buffer = POLICY["min_buffers"].get(head_wallet["id"], head_wallet["min_buffer"])
        hours_left = max((head["due_at"] - now).total_seconds() / 3600, 0)
        remaining = vault["usdc_balance"] - head_action["amount"]

        risk_level = "critical"
        summary = (
            f"{horizon_hours}시간 내 예정 지출 {len(risky)}건에서 자금 부족이 예상된다. "
            f"vault → {head_wallet['id']} 충전이 필요하다."
        )
        reasoning = (
            f"{head_wallet['id']} 지갑의 현재 USDC 잔고는 {head_wallet['usdc_balance']:,.0f}이며, "
            f"{hours_left:,.0f}시간 뒤 지급 예정인 '{head['title']}' 건은 수신자 "
            f"{head['recipient_count']}명에게 총 {head['amount']:,.0f} USDC를 송금해야 한다. "
            f"부족분 {max(head['amount'] - head_wallet['usdc_balance'], 0):,.0f} USDC에 "
            f"정책상 최소 버퍼 {buffer:,.0f} USDC를 더한 {head_action['amount']:,.0f} USDC를 "
            f"{vault['name']}(잔고 {vault['usdc_balance']:,.0f} USDC)에서 이체하면, "
            f"이체 후에도 vault 잔고는 {remaining:,.0f} USDC로 최소 버퍼 "
            f"{POLICY['min_buffers']['vault']:,.0f} USDC를 상회한다. "
        )
        if check["violations"]:
            reasoning += (
                f"다만 정책 검증에서 {check['violations'][0]} 항목에 걸리므로 이 액션은 "
                "자동 실행되지 않으며, 한도 상향 승인 또는 분할 이체 중 하나를 운영자가 선택해야 한다."
            )
        else:
            reasoning += "정책 검증을 모두 통과하므로 승인 즉시 실행 가능하다."
    else:
        risk_level = "safe"
        summary = f"{horizon_hours}시간 내 예정 지출을 현재 잔고로 모두 커버할 수 있다."
        reasoning = (
            "모든 예정 지출에 대해 출금 지갑 잔고가 지급액과 최소 버퍼를 상회한다. "
            "추가 자금 이동은 필요하지 않으며 다음 사이클까지 대기한다."
        )
        actions.append(
            {
                "type": "wait",
                "from_wallet": None,
                "to_wallet": None,
                "amount": None,
                "token": "USDC",
                "execute_by": None,
                "reason": "자금 부족 위험이 감지되지 않아 다음 사이클까지 대기한다.",
            }
        )
        check = validate_actions(actions)

    decision: dict[str, Any] = {
        "id": next_id("dec"),
        "created_at": now,
        "risk_level": risk_level,
        "summary": summary,
        "reasoning": reasoning,
        "at_risk_payments": [p["id"] for p in risky],
        "actions": actions,
        "policy_check": check,
    }
    DECISIONS.append(decision)
    return decision


def get_decision(decision_id: str) -> dict[str, Any] | None:
    return next((d for d in DECISIONS if d["id"] == decision_id), None)


def latest_decision() -> dict[str, Any] | None:
    return DECISIONS[-1] if DECISIONS else None


# --------------------------------------------------------------------------- #
# 실행 (Execution)
# --------------------------------------------------------------------------- #

EXECUTIONS: list[dict[str, Any]] = []


def add_execution(execution: dict[str, Any]) -> dict[str, Any]:
    EXECUTIONS.append(execution)
    return execution


def get_execution(execution_id: str) -> dict[str, Any] | None:
    return next((e for e in EXECUTIONS if e["id"] == execution_id), None)


def find_committed_execution(decision_id: str) -> dict[str, Any] | None:
    """이미 자금이 움직인 실행 기록을 찾는다.

    blocked / simulated 는 자금이 움직이지 않았으므로 재실행을 허용한다.
    """
    return next(
        (
            e
            for e in EXECUTIONS
            if e["decision_id"] == decision_id and e["status"] in ("success", "partial")
        ),
        None,
    )


def make_tx(
    tx_type: str,
    amount: float,
    from_wallet: str | None = None,
    to_wallet: str | None = None,
) -> dict[str, Any]:
    """확정된 트랜잭션 레코드 하나를 만든다."""
    signature = make_signature()
    return {
        "signature": signature,
        "type": tx_type,
        "from_wallet": from_wallet,
        "to_wallet": to_wallet,
        "amount": amount,
        "explorer_url": explorer_url(signature),
        "confirmed_at": utcnow(),
    }


# --------------------------------------------------------------------------- #
# 지갑별 최근 트랜잭션 (시드 데이터)
# --------------------------------------------------------------------------- #

TRANSACTIONS: list[dict[str, Any]] = [
    {
        "signature": "h82pJGF9p7kpzb6eU326EFZf2cDnimbTFVeJtx1qtBmUNJAEqN76R7PwPfHt3oWb8R6cKvhgyxQdDn53jFrK6wFx",
        "type": "transfer",
        "from_wallet": "vault",
        "to_wallet": "operations",
        "amount": 5_000.0,
        "explorer_url": explorer_url(
            "h82pJGF9p7kpzb6eU326EFZf2cDnimbTFVeJtx1qtBmUNJAEqN76R7PwPfHt3oWb8R6cKvhgyxQdDn53jFrK6wFx"
        ),
        "confirmed_at": BOOT_TIME - timedelta(hours=6),
    },
    {
        "signature": "7RJWhvQBQPEjJmki5fhBboGBWRJhmcFkMvrr4Fu3tMSJ5EdynMEiYSyiWAH9GpcbHpeUzeSQF9ZY6q4x8AhBskUf",
        "type": "payment",
        "from_wallet": "operations",
        "to_wallet": None,
        "amount": 890.0,
        "explorer_url": explorer_url(
            "7RJWhvQBQPEjJmki5fhBboGBWRJhmcFkMvrr4Fu3tMSJ5EdynMEiYSyiWAH9GpcbHpeUzeSQF9ZY6q4x8AhBskUf"
        ),
        "confirmed_at": BOOT_TIME - timedelta(hours=19),
    },
    {
        "signature": "5RRfWaHcx1ko8kybqJriN8KUBW1oyoHZqCZ7xhLvhZfDAQqBbra1fMY28QyvtLG4Gyd66oYu5qbr99jXcBHaxfUE",
        "type": "swap",
        "from_wallet": "vault",
        "to_wallet": "vault",
        "amount": 7_000.0,
        "explorer_url": explorer_url(
            "5RRfWaHcx1ko8kybqJriN8KUBW1oyoHZqCZ7xhLvhZfDAQqBbra1fMY28QyvtLG4Gyd66oYu5qbr99jXcBHaxfUE"
        ),
        "confirmed_at": BOOT_TIME - timedelta(hours=31),
    },
    {
        "signature": "bqomDnLSjiQVzaV8GF5N2ecFeF15nh4F5z3xN5ZGJjYEb9oyddXGsXtTD77jUPUTWxo4kii74SoNtx7GDDbV9UCJ",
        "type": "transfer",
        "from_wallet": "vault",
        "to_wallet": "vendor",
        "amount": 12_000.0,
        "explorer_url": explorer_url(
            "bqomDnLSjiQVzaV8GF5N2ecFeF15nh4F5z3xN5ZGJjYEb9oyddXGsXtTD77jUPUTWxo4kii74SoNtx7GDDbV9UCJ"
        ),
        "confirmed_at": BOOT_TIME - timedelta(hours=52),
    },
    {
        "signature": "WGx5Vtxwc74ibv16qwGBTYXExSz4BR1RHssWKUmoscjnYADKE4epb4pM44eXZwaB4Z6wC5f5kxGS8ydGef3g6Tje",
        "type": "payment",
        "from_wallet": "payroll",
        "to_wallet": None,
        "amount": 26_500.0,
        "explorer_url": explorer_url(
            "WGx5Vtxwc74ibv16qwGBTYXExSz4BR1RHssWKUmoscjnYADKE4epb4pM44eXZwaB4Z6wC5f5kxGS8ydGef3g6Tje"
        ),
        "confirmed_at": BOOT_TIME - timedelta(days=7, hours=2),
    },
    {
        "signature": "daMHEjnMGHS9jiLWMq51Wgd75bEZH9Py5yGQKBVvbnLgtia1jucLj7y9H8y7pcAJKfEnNEkhwHZYHzw46hUvJ31N",
        "type": "deposit",
        "from_wallet": None,
        "to_wallet": "vault",
        "amount": 60_000.0,
        "explorer_url": explorer_url(
            "daMHEjnMGHS9jiLWMq51Wgd75bEZH9Py5yGQKBVvbnLgtia1jucLj7y9H8y7pcAJKfEnNEkhwHZYHzw46hUvJ31N"
        ),
        "confirmed_at": BOOT_TIME - timedelta(days=9),
    },
]


def add_transaction(tx: dict[str, Any]) -> dict[str, Any]:
    TRANSACTIONS.insert(0, tx)
    return tx


def wallet_transactions(wallet_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """해당 지갑이 출금 또는 입금에 관여한 트랜잭션을 최신순으로 반환한다."""
    items = [
        tx
        for tx in TRANSACTIONS
        if tx["from_wallet"] == wallet_id or tx["to_wallet"] == wallet_id
    ]
    return sorted(items, key=lambda t: t["confirmed_at"], reverse=True)[:limit]


# --------------------------------------------------------------------------- #
# 로그
# --------------------------------------------------------------------------- #

LOGS: list[dict[str, Any]] = [
    {
        "id": "log_seed_001",
        "type": "cycle",
        "timestamp": BOOT_TIME - timedelta(minutes=12),
        "title": "정기 사이클 실행 (#41)",
        "reasoning": "15분 주기 스케줄러가 잔고를 갱신하고 예정 지출을 재평가했다.",
        "signature": None,
        "explorer_url": None,
        "amount": None,
        "status": "completed",
    },
    {
        "id": "log_seed_002",
        "type": "decision",
        "timestamp": BOOT_TIME - timedelta(hours=6, minutes=4),
        "title": "operations 지갑 충전 판단",
        "reasoning": "API 이용료 결제일이 임박했고 operations 잔고가 버퍼에 근접해 5,000 USDC 충전을 제안했다.",
        "signature": None,
        "explorer_url": None,
        "amount": 5_000.0,
        "status": "executed",
    },
    {
        "id": "log_seed_003",
        "type": "transfer",
        "timestamp": BOOT_TIME - timedelta(hours=6),
        "title": "vault → operations 5,000 USDC 이체",
        "reasoning": "정책 검증 통과 후 자동 실행.",
        "signature": "h82pJGF9p7kpzb6eU326EFZf2cDnimbTFVeJtx1qtBmUNJAEqN76R7PwPfHt3oWb8R6cKvhgyxQdDn53jFrK6wFx",
        "explorer_url": explorer_url(
            "h82pJGF9p7kpzb6eU326EFZf2cDnimbTFVeJtx1qtBmUNJAEqN76R7PwPfHt3oWb8R6cKvhgyxQdDn53jFrK6wFx"
        ),
        "amount": 5_000.0,
        "status": "confirmed",
    },
    {
        "id": "log_seed_004",
        "type": "payment",
        "timestamp": BOOT_TIME - timedelta(hours=19),
        "title": "Solana Pay - Vercel 구독료 결제",
        "reasoning": "x402 결제 요청 수신 후 화이트리스트 확인, 자동 결제.",
        "signature": "7RJWhvQBQPEjJmki5fhBboGBWRJhmcFkMvrr4Fu3tMSJ5EdynMEiYSyiWAH9GpcbHpeUzeSQF9ZY6q4x8AhBskUf",
        "explorer_url": explorer_url(
            "7RJWhvQBQPEjJmki5fhBboGBWRJhmcFkMvrr4Fu3tMSJ5EdynMEiYSyiWAH9GpcbHpeUzeSQF9ZY6q4x8AhBskUf"
        ),
        "amount": 890.0,
        "status": "confirmed",
    },
    {
        "id": "log_seed_005",
        "type": "swap",
        "timestamp": BOOT_TIME - timedelta(hours=31),
        "title": "Jupiter 스왑 40 SOL → 7,000 USDC",
        "reasoning": "USDC 보유고가 임계치 아래로 내려가 SOL 일부를 스왑했다. 슬리피지 18bps로 정책 내.",
        "signature": "5RRfWaHcx1ko8kybqJriN8KUBW1oyoHZqCZ7xhLvhZfDAQqBbra1fMY28QyvtLG4Gyd66oYu5qbr99jXcBHaxfUE",
        "explorer_url": explorer_url(
            "5RRfWaHcx1ko8kybqJriN8KUBW1oyoHZqCZ7xhLvhZfDAQqBbra1fMY28QyvtLG4Gyd66oYu5qbr99jXcBHaxfUE"
        ),
        "amount": 7_000.0,
        "status": "confirmed",
    },
    {
        "id": "log_seed_006",
        "type": "policy",
        "timestamp": BOOT_TIME - timedelta(days=2),
        "title": "정책 변경 - 일일 한도 50,000 USDC",
        "reasoning": "운영자가 월말 정산을 대비해 일일 누적 한도를 40,000에서 50,000으로 상향했다.",
        "signature": None,
        "explorer_url": None,
        "amount": None,
        "status": "applied",
    },
]


def add_log(
    log_type: str,
    title: str,
    reasoning: str | None = None,
    signature: str | None = None,
    explorer: str | None = None,
    amount: float | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """감사 로그를 한 건 추가하고 그 항목을 반환한다."""
    entry: dict[str, Any] = {
        "id": next_id("log"),
        "type": log_type,
        "timestamp": utcnow(),
        "title": title,
        "reasoning": reasoning,
        "signature": signature,
        "explorer_url": explorer or (explorer_url(signature) if signature else None),
        "amount": amount,
        "status": status,
    }
    LOGS.append(entry)
    return entry


def list_logs(
    limit: int = 50, offset: int = 0, log_type: str | None = None
) -> list[dict[str, Any]]:
    """최신순 로그 페이지."""
    items = LOGS if log_type is None else [entry for entry in LOGS if entry["type"] == log_type]
    ordered = sorted(items, key=lambda entry: entry["timestamp"], reverse=True)
    return ordered[offset : offset + limit]


# --------------------------------------------------------------------------- #
# 경보
# --------------------------------------------------------------------------- #

ALERTS: list[dict[str, Any]] = [
    {
        "id": "alert_seed_001",
        "severity": "critical",
        "title": "급여 지갑 자금 부족",
        "message": (
            "payroll 지갑 잔고 5,000 USDC 로는 46시간 뒤 예정된 28,000 USDC 급여 배치를 "
            "지급할 수 없다. vault → payroll 25,000 USDC 이체가 필요하다."
        ),
        "decision_id": None,
        "created_at": BOOT_TIME - timedelta(minutes=12),
        "acknowledged": False,
    },
    {
        "id": "alert_seed_002",
        "severity": "warning",
        "title": "1회 이체 한도로 자동 실행 차단",
        "message": (
            "제안된 25,000 USDC 이체가 1회 한도 10,000 USDC 를 초과해 자동 실행이 "
            "차단되었다. 한도 상향 또는 분할 이체 승인이 필요하다."
        ),
        "decision_id": None,
        "created_at": BOOT_TIME - timedelta(minutes=11),
        "acknowledged": False,
    },
    {
        "id": "alert_seed_003",
        "severity": "info",
        "title": "operations 지갑 충전 완료",
        "message": "vault 에서 operations 로 5,000 USDC 이체가 확정되었다.",
        "decision_id": None,
        "created_at": BOOT_TIME - timedelta(hours=6),
        "acknowledged": True,
    },
]


def list_alerts() -> list[dict[str, Any]]:
    return sorted(ALERTS, key=lambda a: a["created_at"], reverse=True)


def add_alert(
    severity: str, title: str, message: str, decision_id: str | None = None
) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "id": next_id("alert"),
        "severity": severity,
        "title": title,
        "message": message,
        "decision_id": decision_id,
        "created_at": utcnow(),
        "acknowledged": False,
    }
    ALERTS.append(alert)
    return alert


# --------------------------------------------------------------------------- #
# 에이전트 상태
# --------------------------------------------------------------------------- #

AGENT_STATE: dict[str, Any] = {
    "running": True,
    "interval_minutes": 15,
    "last_cycle_at": BOOT_TIME - timedelta(minutes=12),
    "next_cycle_at": BOOT_TIME + timedelta(minutes=3),
    "cycles_today": 41,
    "executions_today": 2,
}


def get_agent_state() -> dict[str, Any]:
    return AGENT_STATE


def record_cycle() -> dict[str, Any]:
    """사이클 1회 실행을 상태에 반영한다."""
    now = utcnow()
    AGENT_STATE["last_cycle_at"] = now
    AGENT_STATE["next_cycle_at"] = now + timedelta(minutes=AGENT_STATE["interval_minutes"])
    AGENT_STATE["cycles_today"] += 1
    return AGENT_STATE


def record_execution() -> dict[str, Any]:
    AGENT_STATE["executions_today"] += 1
    return AGENT_STATE


# --------------------------------------------------------------------------- #
# 스왑 견적
# --------------------------------------------------------------------------- #

# 데모용 고정 환율 (3단계에서 Jupiter Quote API 로 대체)
TOKEN_PRICES_USD: dict[str, float] = {
    "USDC": 1.0,
    "SOL": 176.40,
    "USDT": 0.9998,
    "JUP": 0.82,
}


def quote_swap(input_token: str, output_token: str, amount: float) -> dict[str, Any]:
    """Jupiter 견적을 흉내낸다. 금액이 클수록 price impact 가 커진다."""
    price_in = TOKEN_PRICES_USD.get(input_token.upper())
    price_out = TOKEN_PRICES_USD.get(output_token.upper())
    if price_in is None or price_out is None:
        return {}

    notional = amount * price_in
    price_impact_bps = int(min(12 + notional / 2_000, 480))
    gross = notional / price_out
    estimated_output = round(gross * (1 - price_impact_bps / 10_000), 4)

    route = (
        [input_token.upper(), output_token.upper()]
        if "USDC" in (input_token.upper(), output_token.upper())
        else [input_token.upper(), "USDC", output_token.upper()]
    )

    return {
        "input_token": input_token.upper(),
        "output_token": output_token.upper(),
        "amount": amount,
        "estimated_output": estimated_output,
        "price_impact_bps": price_impact_bps,
        "route": route,
        "within_policy": price_impact_bps <= POLICY["max_slippage_bps"],
        "quote_expires_at": utcnow() + timedelta(seconds=30),
    }
