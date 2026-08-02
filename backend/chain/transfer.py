"""SPL USDC 잔고 조회와 전송.

이 모듈이 온체인 raw 정수를 다루는 유일한 곳이다. 밖으로 나가는 금액은 전부
float USDC 단위이며, 들어오는 금액도 마찬가지다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from solana.exceptions import SolanaRpcException
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.core import RPCException, RPCNoResultException
from solana.rpc.jsonrpc import SolanaJsonRpcError
from solana.rpc.models import TxOpts
from solders.instruction import Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction
from solders.transaction_status import TransactionConfirmationStatus, TransactionStatus
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import (
    create_idempotent_associated_token_account,
    get_associated_token_address,
    transfer_checked,
)
from spl.token.models import TransferCheckedParams

from chain import wallet
from chain.client import (
    CONFIRM_TIMEOUT,
    MAX_SEND_RETRIES,
    MIN_SOL_FOR_FEES,
    USDC_DECIMALS,
    explorer_url,
    get_client,
    get_usdc_mint,
    has_excess_precision,
    lamports_to_sol,
    parse_pubkey,
    to_raw,
    to_ui,
)
from chain.exceptions import (
    InsufficientBalance,
    InsufficientSol,
    InvalidAmount,
    RpcUnavailable,
    TransactionFailed,
)

logger = logging.getLogger(__name__)

# SPL Token Account 바이너리 레이아웃의 amount 필드 위치.
# mint(32) | owner(32) | amount(u64 LE) | ...
_AMOUNT_OFFSET = 64
_AMOUNT_END = _AMOUNT_OFFSET + 8

# confirmation_status 랭크: Processed(0) < Confirmed(1) < Finalized(2)
_CONFIRMED_RANK = int(TransactionConfirmationStatus.Confirmed)

# get_recent_transactions 가 상세 조회를 병렬로 던질 때의 상한.
# 공용 devnet RPC 는 동시 요청이 많으면 429 를 돌려준다.
_TX_DETAIL_CONCURRENCY = 4

# solana-py 의 오류는 두 갈래로 갈라져 나오고 서로 상속 관계가 없다.
#   전송 계층(연결 실패, 타임아웃, 429) -> SolanaRpcException (httpx 예외를 감싼 것)
#   노드가 돌려준 JSON-RPC error   -> RPCException / SolanaJsonRpcError
# 한쪽만 잡으면 나머지가 그대로 500 으로 새어나가므로 항상 둘 다 다룬다.
_TRANSPORT_ERRORS = (SolanaRpcException, OSError)
_RPC_ERRORS = (RPCException, RPCNoResultException, SolanaJsonRpcError)
_ANY_RPC_ERROR = _TRANSPORT_ERRORS + _RPC_ERRORS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# TTL 캐시
# --------------------------------------------------------------------------- #

CACHE_TTL = 10.0

# (kind, address) -> (만료 시각, 값)
_cache: dict[tuple[str, str], tuple[float, Any]] = {}


def _cache_get(kind: str, address: str) -> Any:
    """살아 있는 캐시 값을 반환한다. 없거나 만료면 None."""
    entry = _cache.get((kind, address))
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        _cache.pop((kind, address), None)
        return None
    return value


def _cache_set(kind: str, address: str, value: Any) -> Any:
    _cache[(kind, address)] = (time.monotonic() + CACHE_TTL, value)
    return value


def invalidate_cache(address: Optional[str] = None) -> None:
    """캐시를 무효화한다. address 를 주면 그 주소 항목만, 없으면 전체.

    전송이 확정된 직후 송신·수신 주소 양쪽에 대해 호출해서, 다음 조회가
    10초를 기다리지 않고 바뀐 잔고를 바로 보게 한다.
    """
    if address is None:
        _cache.clear()
        return
    for key in [key for key in _cache if key[1] == address]:
        _cache.pop(key, None)


# --------------------------------------------------------------------------- #
# RPC 헬퍼
# --------------------------------------------------------------------------- #


def _rpc_failure(exc: Exception, what: str) -> RpcUnavailable:
    """RPC 예외를 도메인 예외로 바꾼다. 레이트리밋(429)도 여기로 들어온다."""
    return RpcUnavailable(f"{what} 실패: RPC 노드에 도달할 수 없다. ({exc})")


async def _get_token_account_raw(client: AsyncClient, ata: Pubkey) -> Optional[int]:
    """ATA 의 raw 잔고를 읽는다. 계정이 없으면 None.

    get_token_account_balance 대신 get_account_info 를 쓰는 이유:
    ATA 가 없을 때 전자는 RPC 에러를 던지므로 "계정 없음"과 "노드 장애"를
    구분할 수 없다. 후자는 value=None 으로 조용히 알려준다.
    """
    try:
        resp = await client.get_account_info(ata, commitment=Confirmed)
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "토큰 계정 조회") from exc

    account = resp.value
    if account is None:
        return None

    if account.owner != TOKEN_PROGRAM_ID:
        # ATA 주소에 토큰 프로그램 소유가 아닌 계정이 있다면 우리가 아는 구조가 아니다.
        logger.warning("ATA %s 의 소유 프로그램이 예상과 다르다: %s", ata, account.owner)
        return None

    data = bytes(account.data)
    if len(data) < _AMOUNT_END:
        logger.warning("ATA %s 의 데이터 길이가 %d 바이트로 너무 짧다.", ata, len(data))
        return None

    return int.from_bytes(data[_AMOUNT_OFFSET:_AMOUNT_END], "little")


# --------------------------------------------------------------------------- #
# 잔고 조회
# --------------------------------------------------------------------------- #


async def get_sol_balance(address: str) -> float:
    """주소의 SOL 잔고 (수수료 지불 능력 확인용)."""
    cached = _cache_get("sol", address)
    if cached is not None:
        return cached

    client = get_client()
    pubkey = parse_pubkey(address, "주소")
    try:
        resp = await client.get_balance(pubkey, commitment=Confirmed)
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "SOL 잔고 조회") from exc

    return _cache_set("sol", address, lamports_to_sol(resp.value))


async def get_usdc_balance(address: str) -> float:
    """주소의 USDC 잔고. ATA 가 아직 없으면 예외 대신 0.0.

    지갑에 토큰이 한 번도 들어온 적 없으면 ATA 자체가 존재하지 않는다.
    이건 오류가 아니라 "잔고 0" 이므로 대시보드가 멈추면 안 된다.
    """
    cached = _cache_get("usdc", address)
    if cached is not None:
        return cached

    client = get_client()
    owner = parse_pubkey(address, "주소")
    ata = get_associated_token_address(owner, get_usdc_mint())

    raw = await _get_token_account_raw(client, ata)
    balance = 0.0 if raw is None else to_ui(raw)
    return _cache_set("usdc", address, balance)


async def get_balances(address: str) -> dict[str, float]:
    """USDC / SOL 잔고를 한 번에 조회한다."""
    usdc, sol = await asyncio.gather(
        get_usdc_balance(address), get_sol_balance(address)
    )
    return {"usdc": usdc, "sol": sol}


async def get_all_balances() -> dict[str, dict[str, float]]:
    """레지스트리의 지갑 4개 잔고를 병렬로 조회한다.

    Returns:
        {wallet_id: {"usdc": float, "sol": float}}
    """
    wallet_ids = list(wallet.WALLETS)
    addresses = [wallet.get_address_str(wallet_id) for wallet_id in wallet_ids]
    results = await asyncio.gather(*(get_balances(address) for address in addresses))
    return dict(zip(wallet_ids, results))


async def has_token_account(address: str) -> bool:
    """해당 주소에 USDC ATA 가 존재하는지 확인한다 (진단용)."""
    client = get_client()
    owner = parse_pubkey(address, "주소")
    ata = get_associated_token_address(owner, get_usdc_mint())
    return await _get_token_account_raw(client, ata) is not None


# --------------------------------------------------------------------------- #
# 전송
# --------------------------------------------------------------------------- #


def _validate_amount(amount: float) -> int:
    """금액을 검증하고 raw 정수로 바꾼다."""
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        raise InvalidAmount(f"금액이 숫자가 아니다: {amount!r}")
    if amount != amount or amount in (float("inf"), float("-inf")):  # NaN / inf
        raise InvalidAmount(f"금액이 유효한 수가 아니다: {amount!r}")
    if amount <= 0:
        raise InvalidAmount(f"금액은 0보다 커야 한다: {amount}", amount=amount)
    if has_excess_precision(amount):
        raise InvalidAmount(
            f"USDC 는 소수점 {USDC_DECIMALS}자리까지만 표현할 수 있다: {amount}",
            amount=amount,
        )

    raw = to_raw(amount)
    if raw <= 0:
        raise InvalidAmount(f"금액이 최소 단위보다 작다: {amount}", amount=amount)
    return raw


def _result(
    signature: Optional[str],
    amount: float,
    from_address: str,
    to_address: str,
    dry_run: bool,
    confirmed_at: Optional[datetime],
) -> dict[str, Any]:
    """transfer_usdc 의 반환 형태. mock 구현과 키·타입이 정확히 같아야 한다."""
    return {
        "signature": signature,
        "explorer_url": explorer_url(signature) if signature else None,
        "amount": amount,
        "from": from_address,
        "to": to_address,
        "dry_run": dry_run,
        "confirmed_at": confirmed_at,
    }


async def _build_instructions(
    client: AsyncClient,
    payer: Pubkey,
    source_ata: Pubkey,
    dest_owner: Pubkey,
    mint: Pubkey,
    raw_amount: int,
) -> list[Instruction]:
    """수신 ATA 생성(필요시) + transfer_checked 명령을 만든다.

    ATA 생성을 별도 트랜잭션으로 분리하지 않는다. 분리하면 생성만 성공하고
    전송이 실패하는 중간 상태가 생기고, 실패 지점을 판단하기 어려워진다.
    """
    dest_ata = get_associated_token_address(dest_owner, mint)
    instructions: list[Instruction] = []

    if await _get_token_account_raw(client, dest_ata) is None:
        # idempotent 버전이라 경합으로 그 사이에 계정이 생겨도 실패하지 않는다.
        logger.info("수신자 ATA 가 없어 같은 트랜잭션에 생성 명령을 추가한다: %s", dest_ata)
        instructions.append(
            create_idempotent_associated_token_account(
                payer=payer, owner=dest_owner, mint=mint
            )
        )

    instructions.append(
        transfer_checked(
            TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=source_ata,
                mint=mint,
                dest=dest_ata,
                owner=payer,
                amount=raw_amount,
                # transfer 대신 transfer_checked 를 쓰는 이유가 이 필드다.
                # mint 의 decimals 가 여기와 다르면 런타임이 트랜잭션을 거부한다.
                decimals=USDC_DECIMALS,
            )
        )
    )
    return instructions


async def _await_confirmation(
    client: AsyncClient, signature: Signature, timeout: float
) -> TransactionStatus:
    """서명이 confirmed 될 때까지 폴링한다.

    solana-py 의 confirm_transaction 을 쓰지 않는 이유:
    자체 타임아웃이 90초로 고정이고, err 필드를 확인하지 않아서
    "확정됐지만 실패한" 트랜잭션을 성공으로 돌려준다.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client.get_signature_statuses([signature])
        except _ANY_RPC_ERROR as exc:
            raise _rpc_failure(exc, "서명 상태 조회") from exc

        status = resp.value[0]
        if (
            status is not None
            and status.confirmation_status is not None
            and int(status.confirmation_status) >= _CONFIRMED_RANK
        ):
            return status
        await asyncio.sleep(0.5)

    raise TimeoutError(f"{timeout:.0f}초 안에 확정되지 않았다.")


async def _find_settled(
    client: AsyncClient, signatures: list[str]
) -> Optional[tuple[str, TransactionStatus]]:
    """이미 제출한 서명 중 체인에 자리잡은 것을 찾는다.

    재전송 전에 반드시 거쳐야 하는 단계다. 앞선 시도가 사실은 통과했는데
    응답만 늦게 온 경우, 확인 없이 다시 보내면 같은 금액이 두 번 나간다.
    """
    if not signatures:
        return None

    parsed = [Signature.from_string(sig) for sig in signatures]
    try:
        resp = await client.get_signature_statuses(parsed, search_transaction_history=True)
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "제출된 서명 상태 조회") from exc

    settled = [
        (sig, status) for sig, status in zip(signatures, resp.value) if status is not None
    ]
    if not settled:
        return None

    # 성공한 것이 하나라도 있으면 그것이 결론이다. 앞선 시도가 실패로 끝나고
    # 뒤 시도가 성공한 경우 먼저 찾은 실패를 돌려주면 오판이 된다.
    for sig, status in settled:
        if status.err is None:
            return sig, status
    return settled[0]


async def transfer_usdc(
    from_wallet_id: str,
    to_address: str,
    amount: float,
    dry_run: bool = False,
) -> dict[str, Any]:
    """USDC 를 전송한다.

    Args:
        from_wallet_id: 레지스트리의 지갑 id (vault / operations / payroll / vendor).
        to_address: 수신 주소 (base58). 지갑 id 가 아니라 주소다.
        amount: float USDC. 소수점 6자리까지.
        dry_run: True 면 simulate 까지만 하고 서명·제출하지 않는다.

    Returns:
        signature / explorer_url / amount / from / to / dry_run / confirmed_at.
        dry_run 이면 signature 와 explorer_url, confirmed_at 이 None 이다.

    Raises:
        InvalidAmount: 금액이 0 이하이거나 소수점 6자리 초과.
        InvalidAddress: 수신 주소 파싱 실패.
        InsufficientBalance: 송신 USDC 잔고 부족.
        InsufficientSol: 수수료 + ATA rent 용 SOL 부족.
        TransactionFailed: 온체인 실패 또는 재시도 후에도 확정 실패.
        RpcUnavailable: RPC 노드 장애 / 레이트리밋.
    """
    # 1. 금액 검증 — 네트워크를 건드리기 전에 끝낸다.
    raw_amount = _validate_amount(amount)

    # 2. 서명 키 로드. 이 객체는 절대 로그에 남기지 않는다.
    keypair = wallet.load_keypair(from_wallet_id)
    sender = keypair.pubkey()
    from_address = str(sender)

    # 3. 주소 파싱
    recipient = parse_pubkey(to_address, "수신 주소")

    client = get_client()
    mint = get_usdc_mint()

    # 4~5. 잔고 확인. 온체인 실패로 수수료를 태우기 전에 여기서 막는다.
    # 이 값은 최대 CACHE_TTL 초 묵은 것일 수 있다. 그 사이 잔고가 줄었다면
    # 여기를 통과하더라도 노드의 preflight 가 잡아내므로 자금이 잘못 나가지는
    # 않는다. 다만 그 경우 409 대신 502(TransactionFailed)로 떨어진다.
    usdc_balance, sol_balance = await asyncio.gather(
        get_usdc_balance(from_address), get_sol_balance(from_address)
    )
    if usdc_balance < amount:
        raise InsufficientBalance(
            required=amount, available=usdc_balance, address=from_address
        )
    if sol_balance < MIN_SOL_FOR_FEES:
        raise InsufficientSol(
            required=MIN_SOL_FOR_FEES, available=sol_balance, address=from_address
        )

    # 6~7. 수신 ATA 확인 + 명령 구성
    source_ata = get_associated_token_address(sender, mint)
    instructions = await _build_instructions(
        client, sender, source_ata, recipient, mint, raw_amount
    )

    logger.info(
        "USDC 전송 준비: %s -> %s, %s USDC (명령 %d개, dry_run=%s)",
        from_wallet_id,
        to_address,
        amount,
        len(instructions),
        dry_run,
    )

    # 8. dry_run 이면 시뮬레이션까지만.
    if dry_run:
        await _simulate(client, sender, instructions)
        return _result(None, amount, from_address, to_address, True, None)

    # 9. 서명 → 전송 → 확정. 실패하면 새 blockhash 로 재시도한다.
    signature, confirmed_at = await _send_and_confirm(client, keypair, instructions)

    # 잔고가 바뀌었으므로 양쪽 캐시를 버려 다음 조회가 즉시 반영되게 한다.
    invalidate_cache(from_address)
    invalidate_cache(to_address)

    logger.info("USDC 전송 확정: %s (%s USDC)", signature, amount)
    return _result(signature, amount, from_address, to_address, False, confirmed_at)


async def _simulate(
    client: AsyncClient, payer: Pubkey, instructions: list[Instruction]
) -> None:
    """서명 없이 시뮬레이션한다. 실패하면 TransactionFailed."""
    try:
        blockhash = (await client.get_latest_blockhash(Confirmed)).value.blockhash
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "blockhash 조회") from exc

    message = MessageV0.try_compile(
        payer=payer,
        instructions=instructions,
        address_lookup_table_accounts=[],
        recent_blockhash=blockhash,
    )
    # populate 는 서명 자리를 빈 값으로 채운다. 실제 서명은 하지 않는다.
    unsigned = VersionedTransaction.populate(message, [Signature.default()])

    try:
        resp = await client.simulate_transaction(unsigned, sig_verify=False, commitment=Confirmed)
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "시뮬레이션") from exc

    result = resp.value
    if result.err is not None:
        raise TransactionFailed(
            f"시뮬레이션 실패: {result.err}. 실제 전송은 하지 않았다.",
            simulation_logs=list(result.logs or [])[-10:],
        )

    logger.info(
        "시뮬레이션 통과 (compute units: %s)\n%s",
        result.units_consumed,
        "\n".join(result.logs or []),
    )


async def _send_and_confirm(
    client: AsyncClient, keypair: Any, instructions: list[Instruction]
) -> tuple[str, datetime]:
    """서명·전송·확정을 최대 MAX_SEND_RETRIES 회까지 재시도한다.

    재시도는 blockhash 만료와 확정 타임아웃에 대해서만 한다. 온체인에서
    실패로 확정된 트랜잭션은 원인이 결정적이므로 즉시 포기한다.
    """
    submitted: list[str] = []
    last_reason = ""

    for attempt in range(MAX_SEND_RETRIES + 1):
        # 재전송 전에 앞 시도가 뒤늦게 체결됐는지 먼저 본다. 중복 전송 방지의 핵심.
        settled = await _find_settled(client, submitted)
        if settled is not None:
            sig, status = settled
            if status.err is None:
                logger.info("재확인 중 앞선 시도가 확정된 것을 발견했다: %s", sig)
                return sig, _utcnow()
            raise TransactionFailed(
                f"트랜잭션이 온체인에서 실패했다: {status.err}", signature=sig
            )

        try:
            latest = (await client.get_latest_blockhash(Confirmed)).value
        except _ANY_RPC_ERROR as exc:
            raise _rpc_failure(exc, "blockhash 조회") from exc

        message = MessageV0.try_compile(
            payer=keypair.pubkey(),
            instructions=instructions,
            address_lookup_table_accounts=[],
            recent_blockhash=latest.blockhash,
        )
        transaction = VersionedTransaction(message, [keypair])

        # 서명은 전송하기 전에 이미 확정돼 있다. 응답을 못 받는 경우에도
        # 무엇을 조회해야 하는지 알 수 있도록 미리 기록해둔다.
        signature = str(transaction.signatures[0])
        submitted.append(signature)

        try:
            await client.send_transaction(
                transaction,
                opts=TxOpts(
                    skip_confirmation=True,
                    skip_preflight=False,
                    # 기본값 Finalized 로 두면 방금 받은 blockhash 를 아직 모른다며
                    # preflight 가 "Blockhash not found" 로 떨어질 수 있다.
                    preflight_commitment=Confirmed,
                ),
            )
        except _RPC_ERRORS as exc:
            # 노드가 시뮬레이션 후 거부했다. 전파되지 않았음이 확실하므로
            # 이 서명은 "확인이 필요한 제출 이력"에서 도로 뺀다.
            submitted.pop()
            raise TransactionFailed(
                f"트랜잭션이 노드에서 거부됐다 (preflight): {exc}",
                signatures=submitted,
            ) from exc
        except _TRANSPORT_ERRORS as exc:
            # 응답을 못 받았을 뿐, 트랜잭션이 전파됐을 가능성은 남아 있다.
            # 서명을 남겨둔 채 올려보내 운영자가 체결 여부를 확인하게 한다.
            raise TransactionFailed(
                f"트랜잭션 전송 중 RPC 응답을 받지 못했다: {exc}",
                signatures=submitted,
            ) from exc

        logger.info("트랜잭션 제출 (시도 %d): %s", attempt + 1, signature)

        try:
            status = await _await_confirmation(
                client, transaction.signatures[0], CONFIRM_TIMEOUT
            )
        except TimeoutError:
            last_reason = f"확정 대기 {CONFIRM_TIMEOUT:.0f}초 초과"
            logger.warning("%s: %s — 재시도한다.", last_reason, signature)
            continue

        if status.err is not None:
            raise TransactionFailed(
                f"트랜잭션이 온체인에서 실패했다: {status.err}", signature=signature
            )
        return signature, _utcnow()

    raise TransactionFailed(
        f"{MAX_SEND_RETRIES + 1}회 시도했지만 확정하지 못했다 ({last_reason}).",
        signatures=submitted,
    )


# --------------------------------------------------------------------------- #
# 최근 트랜잭션
# --------------------------------------------------------------------------- #


async def get_recent_transactions(address: str, limit: int = 10) -> list[dict[str, Any]]:
    """주소의 최근 USDC 관련 트랜잭션을 최신순으로 반환한다.

    반환 dict 는 schemas.TxRecord 와 같은 모양이다:
    signature / type / from_wallet / to_wallet / amount / explorer_url / confirmed_at.

    금액은 meta 의 token balance 전후 차이에서 계산한다. meta 를 못 읽는
    트랜잭션은 amount 0.0 으로 남겨두고 버리지는 않는다.
    """
    cached = _cache_get(f"txs:{limit}", address)
    if cached is not None:
        return cached

    client = get_client()
    owner = parse_pubkey(address, "주소")

    try:
        resp = await client.get_signatures_for_address(owner, limit=limit, commitment=Confirmed)
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "트랜잭션 목록 조회") from exc

    entries = [item for item in resp.value if item.err is None]
    if not entries:
        return _cache_set(f"txs:{limit}", address, [])

    semaphore = asyncio.Semaphore(_TX_DETAIL_CONCURRENCY)

    async def detail(signature: Signature) -> Optional[Any]:
        async with semaphore:
            try:
                detail_resp = await client.get_transaction(
                    signature,
                    encoding="json",
                    commitment=Confirmed,
                    max_supported_transaction_version=0,
                )
                return detail_resp.value
            except _ANY_RPC_ERROR as exc:
                # 상세를 못 읽어도 목록 자체는 보여준다.
                logger.warning("트랜잭션 %s 상세 조회 실패: %s", signature, exc)
                return None

    details = await asyncio.gather(*(detail(item.signature) for item in entries))

    records = [
        _to_tx_record(item, value, address)
        for item, value in zip(entries, details)
    ]
    return _cache_set(f"txs:{limit}", address, records)


def _to_tx_record(entry: Any, detail: Any, address: str) -> dict[str, Any]:
    """서명 항목 + 상세를 TxRecord 모양의 dict 로 만든다."""
    signature = str(entry.signature)
    confirmed_at = (
        datetime.fromtimestamp(entry.block_time, tz=timezone.utc)
        if entry.block_time
        else _utcnow()
    )

    delta, counterparty = _usdc_delta(detail, address)
    self_id = wallet.resolve_wallet_id(address)
    other_id = wallet.resolve_wallet_id(counterparty) if counterparty else None

    if delta > 0:
        # 들어온 거래. 상대가 우리 지갑이면 내부 이체, 아니면 외부 입금.
        tx_type = "transfer" if other_id else "deposit"
        from_wallet, to_wallet = other_id, self_id
    elif delta < 0:
        # 나간 거래. 외부로 나갔으면 결제로 본다.
        tx_type = "transfer" if other_id else "payment"
        from_wallet, to_wallet = self_id, other_id
    else:
        tx_type = "transfer"
        from_wallet, to_wallet = self_id, other_id

    return {
        "signature": signature,
        "type": tx_type,
        "from_wallet": from_wallet,
        "to_wallet": to_wallet,
        "amount": abs(delta),
        "explorer_url": explorer_url(signature),
        "confirmed_at": confirmed_at,
    }


def _usdc_delta(detail: Any, address: str) -> tuple[float, Optional[str]]:
    """트랜잭션에서 해당 주소의 USDC 증감과 상대 주소를 뽑는다.

    Returns:
        (delta, counterparty_address). 판단할 수 없으면 (0.0, None).
    """
    meta = getattr(getattr(detail, "transaction", None), "meta", None)
    if meta is None:
        return 0.0, None

    mint = str(get_usdc_mint())

    def by_owner(balances: Any) -> dict[str, int]:
        """owner 주소 -> raw 잔고. USDC mint 것만 남긴다."""
        totals: dict[str, int] = {}
        for balance in balances or []:
            if str(balance.mint) != mint or balance.owner is None:
                continue
            owner = str(balance.owner)
            totals[owner] = totals.get(owner, 0) + int(balance.ui_token_amount.amount)
        return totals

    pre = by_owner(meta.pre_token_balances)
    post = by_owner(meta.post_token_balances)

    deltas = {
        owner: post.get(owner, 0) - pre.get(owner, 0)
        for owner in set(pre) | set(post)
    }
    mine = deltas.get(address, 0)
    if mine == 0:
        return 0.0, None

    # 부호가 반대이면서 절대값이 가장 큰 상대를 카운터파티로 본다.
    counterparties = [
        (owner, value)
        for owner, value in deltas.items()
        if owner != address and value * mine < 0
    ]
    counterparty = (
        max(counterparties, key=lambda pair: abs(pair[1]))[0] if counterparties else None
    )
    return to_ui(mine), counterparty


# --------------------------------------------------------------------------- #
# devnet 편의 기능
# --------------------------------------------------------------------------- #


async def request_airdrop(address: str, sol_amount: float = 1.0) -> str:
    """devnet faucet 에서 SOL 을 받는다. mainnet 에서는 동작하지 않는다."""
    from chain.client import sol_to_lamports

    client = get_client()
    pubkey = parse_pubkey(address, "주소")
    try:
        resp = await client.request_airdrop(pubkey, sol_to_lamports(sol_amount))
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "airdrop 요청") from exc

    signature = resp.value
    try:
        await _await_confirmation(client, signature, CONFIRM_TIMEOUT)
    except TimeoutError as exc:
        raise TransactionFailed(
            "airdrop 이 확정되지 않았다. devnet faucet 이 혼잡할 수 있다.",
            signature=str(signature),
        ) from exc

    invalidate_cache(address)
    return str(signature)


async def get_transaction(signature: str) -> Optional[dict[str, Any]]:
    """서명으로 트랜잭션 상세를 조회한다. 없으면 None."""
    client = get_client()
    try:
        parsed = Signature.from_string(signature)
    except ValueError as exc:
        from chain.exceptions import InvalidAddress

        raise InvalidAddress(f"유효한 트랜잭션 서명이 아니다: {signature}") from exc

    try:
        resp = await client.get_transaction(
            parsed, encoding="json", commitment=Confirmed, max_supported_transaction_version=0
        )
    except _ANY_RPC_ERROR as exc:
        raise _rpc_failure(exc, "트랜잭션 조회") from exc

    if resp.value is None:
        return None

    meta = getattr(resp.value.transaction, "meta", None)
    return {
        "signature": signature,
        "slot": resp.value.slot,
        "block_time": (
            datetime.fromtimestamp(resp.value.block_time, tz=timezone.utc)
            if resp.value.block_time
            else None
        ),
        "err": None if meta is None or meta.err is None else str(meta.err),
        "fee": lamports_to_sol(meta.fee) if meta is not None else None,
        "explorer_url": explorer_url(signature),
    }
