#!/usr/bin/env python3
"""트레저리 지갑 4개의 온체인 상태를 표로 출력한다.

devnet 연동이 제대로 됐는지 확인하는 첫 번째 도구다. 전송 테스트도 겸한다.

사용법:
    cd backend
    python scripts/check_balances.py                       # 잔고 표
    python scripts/check_balances.py --json                # 기계가 읽을 형태
    python scripts/check_balances.py --airdrop vault       # devnet SOL 받기

    # 전송 테스트 — 먼저 --dry-run 으로 시뮬레이션할 것
    python scripts/check_balances.py --from vault --to payroll --amount 10 --dry-run
    python scripts/check_balances.py --from vault --to payroll --amount 10

    --to 에는 지갑 id 와 base58 주소 둘 다 넣을 수 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# backend/ 를 import 경로에 넣어 chain 패키지를 찾게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chain import transfer, wallet
from chain.client import (
    MIN_SOL_FOR_FEES,
    NETWORK,
    RPC_URL,
    USDC_MINT_STR,
    close_client,
    explorer_address_url,
)
from chain.exceptions import SolanaError


async def collect(wallet_id: str) -> dict[str, Any]:
    """지갑 하나의 상태를 모은다. 실패해도 표는 그려야 하므로 예외를 담아둔다."""
    row: dict[str, Any] = {"id": wallet_id, "name": wallet.WALLETS[wallet_id].name}
    try:
        address = wallet.get_address_str(wallet_id)
    except SolanaError as exc:
        return {**row, "address": None, "error": exc.detail}

    row["address"] = address

    # 항목별로 따로 받는다. USDC_MINT 가 비어 있어도 SOL 잔고는 보여야
    # "지갑에 가스비가 있는지" 라는 첫 질문에 답할 수 있기 때문이다.
    sol, usdc, ata = await asyncio.gather(
        transfer.get_sol_balance(address),
        transfer.get_usdc_balance(address),
        transfer.has_token_account(address),
        return_exceptions=True,
    )

    errors = [
        exc.detail if isinstance(exc, SolanaError) else str(exc)
        for exc in (sol, usdc, ata)
        if isinstance(exc, BaseException)
    ]
    row.update(
        sol=None if isinstance(sol, BaseException) else sol,
        usdc=None if isinstance(usdc, BaseException) else usdc,
        ata=None if isinstance(ata, BaseException) else ata,
        # 같은 원인(대개 USDC_MINT 미설정)이 여러 번 나오므로 중복은 지운다.
        error="; ".join(dict.fromkeys(errors)) or None,
    )
    return row


def render(rows: list[dict[str, Any]]) -> None:
    """잔고 표를 출력한다."""
    print(f"\n  network : {NETWORK}")
    print(f"  rpc     : {RPC_URL}")
    print(f"  mint    : {USDC_MINT_STR or '(USDC_MINT 미설정)'}\n")

    header = f"  {'WALLET':<12} {'ADDRESS':<46} {'USDC':>14} {'SOL':>12}  {'ATA':<5}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for row in rows:
        if row["address"] is None:
            print(f"  {row['id']:<12} {'(주소 없음)':<46} {'-':>14} {'-':>12}  {'-':<5}")
            continue

        usdc = "error" if row["usdc"] is None else f"{row['usdc']:,.6f}"
        sol = "error" if row["sol"] is None else f"{row['sol']:,.6f}"
        ata = "-" if row["ata"] is None else ("yes" if row["ata"] else "NO")

        # 수수료를 못 낼 정도로 SOL 이 적으면 눈에 띄게 표시한다.
        flag = ""
        if row["sol"] is not None and row["sol"] < MIN_SOL_FOR_FEES:
            flag = f"  <- SOL 부족 (최소 {MIN_SOL_FOR_FEES})"

        print(f"  {row['id']:<12} {row['address']:<46} {usdc:>14} {sol:>12}  {ata:<5}{flag}")

    print()
    for row in rows:
        if row.get("error"):
            print(f"  ! {row['id']}: {row['error']}")
        elif row.get("ata") is False:
            print(
                f"  · {row['id']}: USDC ATA 가 아직 없다 (잔고 0). "
                "이 지갑으로 첫 전송을 하면 같은 트랜잭션에서 자동 생성된다."
            )
    print()
    for row in rows:
        if row["address"]:
            print(f"  {row['id']:<12} {explorer_address_url(row['address'])}")
    print()


def resolve_target(value: str) -> str:
    """지갑 id 면 주소로 바꾸고, 이미 주소면 그대로 둔다."""
    if value in wallet.WALLETS:
        return wallet.get_address_str(value)
    return value


async def do_transfer(
    from_wallet: str, to: str, amount: float, dry_run: bool
) -> dict[str, Any]:
    to_address = resolve_target(to)
    label = "시뮬레이션" if dry_run else "전송"
    print(f"\n  {label}: {from_wallet} -> {to} ({to_address})  {amount} USDC")

    result = await transfer.transfer_usdc(from_wallet, to_address, amount, dry_run=dry_run)

    if dry_run:
        print("  시뮬레이션 통과. 실제 서명·제출은 하지 않았다.")
        print("  --dry-run 을 빼고 다시 실행하면 실제로 전송된다.\n")
    else:
        print(f"  확정: {result['signature']}")
        print(f"  {result['explorer_url']}\n")
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="트레저리 지갑 잔고 확인 및 USDC 전송 테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--json", action="store_true", help="표 대신 JSON 으로 출력")
    parser.add_argument("--airdrop", metavar="WALLET", help="지갑에 devnet SOL 1개 요청")
    parser.add_argument("--from", dest="from_wallet", metavar="WALLET", help="출금 지갑 id")
    parser.add_argument("--to", metavar="WALLET|ADDRESS", help="수신 지갑 id 또는 주소")
    parser.add_argument("--amount", type=float, metavar="USDC", help="전송 금액 (USDC)")
    parser.add_argument(
        "--dry-run", action="store_true", help="시뮬레이션만 하고 제출하지 않음"
    )
    parser.add_argument("--verbose", action="store_true", help="RPC 상세 로그 출력")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="  [%(levelname)s] %(message)s",
    )

    transfer_args = [args.from_wallet, args.to, args.amount]
    if any(x is not None for x in transfer_args) and not all(
        x is not None for x in transfer_args
    ):
        parser.error("--from, --to, --amount 는 셋 다 지정해야 한다.")

    missing = wallet.validate_env()
    if missing:
        print(f"\n  ! 비어 있는 환경변수: {', '.join(sorted(set(missing)))}")
        print("    .env.example 을 참고해 .env 를 채울 것.")

    for address, wallet_ids in wallet.detect_duplicate_addresses().items():
        print(f"\n  ! {' / '.join(wallet_ids)} 이 같은 주소를 쓴다: {address}")
        print("    .env 의 프라이빗 키가 중복됐다. 지갑별로 다른 키를 넣을 것.")

    try:
        if args.airdrop:
            address = wallet.get_address_str(args.airdrop)
            print(f"\n  {args.airdrop} ({address}) 에 devnet SOL 1개 요청 중...")
            signature = await transfer.request_airdrop(address, 1.0)
            print(f"  확정: {signature}\n")

        result: dict[str, Any] | None = None
        if args.from_wallet:
            result = await do_transfer(
                args.from_wallet, args.to, args.amount, args.dry_run
            )

        rows = await asyncio.gather(*(collect(wallet_id) for wallet_id in wallet.WALLETS))

        if args.json:
            print(json.dumps({"wallets": list(rows), "transfer": result}, indent=2, default=str))
        else:
            render(list(rows))

    except SolanaError as exc:
        # 온체인 도메인 오류는 스택트레이스 없이 사람이 읽을 메시지로만 보여준다.
        print(f"\n  ! [{exc.code}] {exc.detail}\n", file=sys.stderr)
        return 1
    finally:
        await close_client()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
