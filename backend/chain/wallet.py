"""트레저리 지갑 레지스트리와 키 로딩.

보안 원칙
---------
프라이빗 키는 이 모듈 밖으로 문자열 형태로 나가지 않는다.
- 레지스트리(WalletInfo)에는 공개 주소만 담는다.
- load_keypair() 는 호출 시점에 os.environ 에서 직접 읽고 지역 변수로만 다룬다.
- solders 의 Keypair.__repr__ 은 signing key 바이트를 그대로 찍는다.
  따라서 Keypair 객체를 절대 로그·응답·f-string 에 넣지 말 것.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from chain.client import parse_pubkey
from chain.exceptions import ConfigError


def create_wallet() -> dict[str, str]:
    """새 지갑 생성"""
    kp = Keypair()
    private_key = base58.b58encode(bytes(kp)).decode()
    public_key = str(kp.pubkey())
    return {"public_key": public_key, "private_key": private_key}


# --------------------------------------------------------------------------- #
# 레지스트리
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WalletInfo:
    """트레저리 지갑 하나의 메타데이터. 비밀값은 담지 않는다."""

    id: str
    name: str
    type: str
    min_buffer: float
    public_key_env: str
    private_key_env: str
    # 이전 단계의 .env 에서 쓰던 키 이름. 하위 호환용 폴백.
    legacy_public_key_env: str | None = None
    legacy_private_key_env: str | None = None
    _address_cache: dict[str, str] = field(
        default_factory=dict, repr=False, compare=False
    )

    @property
    def address(self) -> str | None:
        """공개 주소. *_PUBLIC_KEY 가 없으면 프라이빗 키에서 유도한다.

        유도 결과는 캐시한다. 매 조회마다 base58 디코드 + ed25519 연산을 하지
        않기 위함이며, 캐시에는 공개 주소만 들어간다.
        """
        explicit = _read_env(self.public_key_env, self.legacy_public_key_env)
        if explicit:
            return explicit

        if "derived" in self._address_cache:
            return self._address_cache["derived"] or None

        secret = _read_env(self.private_key_env, self.legacy_private_key_env)
        derived = ""
        if secret:
            try:
                derived = str(_keypair_from_secret(secret, self.id).pubkey())
            except ConfigError:
                derived = ""
        self._address_cache["derived"] = derived
        return derived or None

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의용
        """비밀값이 섞여 들어갈 여지를 없애기 위해 주소만 노출한다."""
        return f"WalletInfo(id={self.id!r}, type={self.type!r}, address={self.address!r})"


WALLETS: dict[str, WalletInfo] = {
    "vault": WalletInfo(
        id="vault",
        name="Treasury Vault",
        type="vault",
        min_buffer=10_000.0,
        public_key_env="VAULT_PUBLIC_KEY",
        private_key_env="VAULT_PRIVATE_KEY",
        legacy_public_key_env="TREASURY_VAULT_PUBLIC_KEY",
        legacy_private_key_env="TREASURY_VAULT_PRIVATE_KEY",
    ),
    "operations": WalletInfo(
        id="operations",
        name="Operations",
        type="operations",
        min_buffer=1_000.0,
        public_key_env="OPERATIONS_PUBLIC_KEY",
        private_key_env="OPERATIONS_PRIVATE_KEY",
        legacy_public_key_env="OPERATIONS_WALLET_PUBLIC_KEY",
        legacy_private_key_env="OPERATIONS_WALLET_PRIVATE_KEY",
    ),
    "payroll": WalletInfo(
        id="payroll",
        name="Payroll",
        type="payroll",
        min_buffer=2_000.0,
        public_key_env="PAYROLL_PUBLIC_KEY",
        private_key_env="PAYROLL_PRIVATE_KEY",
        legacy_public_key_env="PAYROLL_WALLET_PUBLIC_KEY",
        legacy_private_key_env="PAYROLL_WALLET_PRIVATE_KEY",
    ),
    "vendor": WalletInfo(
        id="vendor",
        name="Vendor Settlement",
        type="vendor",
        min_buffer=1_000.0,
        public_key_env="VENDOR_PUBLIC_KEY",
        private_key_env="VENDOR_PRIVATE_KEY",
        legacy_public_key_env="VENDOR_WALLET_PUBLIC_KEY",
        legacy_private_key_env="VENDOR_WALLET_PRIVATE_KEY",
    ),
}


# --------------------------------------------------------------------------- #
# 내부 헬퍼
# --------------------------------------------------------------------------- #


def _read_env(name: str, legacy: str | None = None) -> str:
    """환경변수를 읽고 따옴표/공백을 정리한다. legacy 이름은 폴백."""
    for candidate in (name, legacy):
        if not candidate:
            continue
        value = os.getenv(candidate, "").strip().strip("'\"")
        if value:
            return value
    return ""


def _keypair_from_secret(secret: str, wallet_id: str) -> Keypair:
    """비밀키 문자열을 Keypair 로 만든다.

    create_wallet() 이 저장하는 형식은 bytes(kp) 64바이트의 base58 인코딩이다.
    운영자가 solana-keygen 출력(JSON 배열)이나 32바이트 seed 를 붙여넣는 경우도
    흔해서 함께 받아준다.
    """
    raw: bytes
    if secret.startswith("["):
        try:
            raw = bytes(int(part) for part in secret.strip("[]").split(",") if part.strip())
        except ValueError as exc:
            raise ConfigError(
                f"'{wallet_id}' 프라이빗 키를 JSON 바이트 배열로 해석할 수 없다."
            ) from exc
    else:
        try:
            raw = base58.b58decode(secret)
        except Exception as exc:  # base58 은 여러 예외를 던진다
            raise ConfigError(
                f"'{wallet_id}' 프라이빗 키가 유효한 base58 문자열이 아니다."
            ) from exc

    try:
        if len(raw) == 64:
            return Keypair.from_bytes(raw)
        if len(raw) == 32:
            return Keypair.from_seed(raw)
    except ValueError as exc:
        raise ConfigError(f"'{wallet_id}' 프라이빗 키를 Keypair 로 만들 수 없다.") from exc

    raise ConfigError(
        f"'{wallet_id}' 프라이빗 키 길이가 {len(raw)} 바이트다. "
        "64바이트(keypair) 또는 32바이트(seed) 여야 한다."
    )


def get_wallet(wallet_id: str) -> WalletInfo:
    """지갑 메타데이터를 반환한다. 없는 id 면 ConfigError."""
    wallet = WALLETS.get(wallet_id)
    if wallet is None:
        raise ConfigError(
            f"알 수 없는 지갑 id '{wallet_id}'. 사용 가능: {', '.join(WALLETS)}"
        )
    return wallet


# --------------------------------------------------------------------------- #
# 공개 API
# --------------------------------------------------------------------------- #


def load_keypair(wallet_id: str) -> Keypair:
    """서명용 Keypair 를 로드한다.

    반환된 객체를 로그·응답에 넣지 말 것 (Keypair.__repr__ 이 signing key 를 노출).

    Raises:
        ConfigError: 환경변수 누락, 형식 오류, 또는 *_PUBLIC_KEY 와 불일치.
    """
    wallet = get_wallet(wallet_id)
    secret = _read_env(wallet.private_key_env, wallet.legacy_private_key_env)
    if not secret:
        raise ConfigError(
            f"'{wallet_id}' 지갑의 프라이빗 키가 없다. "
            f".env 에 {wallet.private_key_env} 를 설정해야 한다."
        )

    keypair = _keypair_from_secret(secret, wallet_id)

    # 공개 주소를 따로 적어둔 경우, 실제 서명 키와 일치하는지 확인한다.
    # 불일치하면 의도한 지갑이 아닌 곳에서 자금이 나간다.
    declared = _read_env(wallet.public_key_env, wallet.legacy_public_key_env)
    if declared and declared != str(keypair.pubkey()):
        raise ConfigError(
            f"'{wallet_id}' 지갑 설정 불일치: {wallet.public_key_env} 는 {declared} 인데 "
            f"{wallet.private_key_env} 에서 유도된 주소는 {keypair.pubkey()} 다. "
            "잘못된 지갑에서 자금이 나갈 수 있으므로 중단한다."
        )
    return keypair


def get_address(wallet_id: str) -> Pubkey:
    """지갑의 공개 주소를 Pubkey 로 반환한다."""
    wallet = get_wallet(wallet_id)
    address = wallet.address
    if not address:
        raise ConfigError(
            f"'{wallet_id}' 지갑의 주소를 알 수 없다. "
            f".env 에 {wallet.public_key_env} 또는 {wallet.private_key_env} 가 필요하다."
        )
    return parse_pubkey(address, f"{wallet_id} 지갑 주소")


def get_address_str(wallet_id: str) -> str:
    """지갑의 공개 주소를 base58 문자열로 반환한다."""
    return str(get_address(wallet_id))


def resolve_wallet_id(address: str) -> str | None:
    """주소로 내부 지갑 id 를 역조회한다. 외부 주소면 None."""
    for wallet_id, wallet in WALLETS.items():
        if wallet.address == address:
            return wallet_id
    return None


def validate_env() -> list[str]:
    """누락된 환경변수 이름 목록을 반환한다. 빈 리스트면 정상.

    서버 startup 에서 호출해 경고 로그를 남기는 용도다. 여기서 예외를 던지면
    MOCK_MODE 로 시연하려는 경우까지 서버가 못 뜨므로 목록만 돌려준다.
    """
    from chain.client import RPC_URL, USDC_MINT_STR

    missing: list[str] = []

    if not RPC_URL:
        missing.append("SOLANA_RPC_URL")
    if not USDC_MINT_STR:
        missing.append("USDC_MINT")

    for wallet in WALLETS.values():
        if not _read_env(wallet.private_key_env, wallet.legacy_private_key_env):
            missing.append(wallet.private_key_env)
        if not wallet.address:
            missing.append(wallet.public_key_env)

    return missing


def detect_duplicate_addresses() -> dict[str, list[str]]:
    """같은 주소를 공유하는 지갑들을 찾는다.

    서로 다른 지갑에 같은 프라이빗 키를 넣어두면 vault -> payroll 이체가
    자기 자신에게 보내는 무의미한 트랜잭션이 되고, 정책의 지갑별 최소 버퍼도
    무너진다. 자금이 잘못 움직이기 전에 startup 에서 잡아야 한다.

    Returns:
        {주소: [지갑 id, ...]} — 2개 이상 겹치는 것만.
    """
    by_address: dict[str, list[str]] = {}
    for wallet_id, wallet in WALLETS.items():
        address = wallet.address
        if address:
            by_address.setdefault(address, []).append(wallet_id)
    return {
        address: ids for address, ids in by_address.items() if len(ids) > 1
    }


if __name__ == "__main__":
    # 이 파일 직접 실행하면 지갑 4개 생성됨
    for wallet_id, info in WALLETS.items():
        w = create_wallet()
        print(f"\n{info.name} ({wallet_id}):")
        print(f"  {info.public_key_env}={w['public_key']}")
        print(f"  {info.private_key_env}={w['private_key']}")
    print(
        "\n→ 위 줄을 .env 에 그대로 붙여넣고, Public Key 는 devnet faucet 에서 SOL 을 받을 것."
    )
