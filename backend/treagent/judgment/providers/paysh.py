"""x402 결제를 거쳐 Gemini 를 부르는 공급자 — **기본 경로다.**

왜 API 키가 아니라 결제인가
---------------------------
요구사항은 "AI 에이전트가 사람 개입 없이 정해진 한도 안에서 x402 로 클라우드
API 를 발견·인증·결제한다" 이다. API 키는 사람이 미리 발급해 심어두는 것이므로
그 요구를 만족하지 않는다. 이 공급자는 402 를 받고, USDC 를 내고, 200 을 받는다.

그리고 그 지출은 이 에이전트가 관리하는 Operations Wallet 의 예정 지출이 된다.
**에이전트가 자기 추론 비용을 스스로 결제하고, 그 지출이 다음 사이클의 예측
대상이 되는** 재귀 루프가 데모의 핵심이다 (:mod:`treagent.judgment.inference_cost`).

네트워크 분리 — 반드시 알아둘 것
--------------------------------
pay.sh sandbox 와 TreAgent 의 Solana Devnet 은 **별도 네트워크다.** pay CLI 에
``--devnet`` 모드는 없다 (``--sandbox`` / ``--local`` / ``--mainnet`` 뿐).
따라서 x402 결제는 Surfpool localnet 지갑에서 나가며, Devnet Operations Wallet
에서 차감되지 않는다. 그 둘을 같은 것처럼 표시하면 거짓말이 된다 —
:class:`PayShSnapshot` 이 ``sandbox_balance_*`` 와 ``devnet_ops_balance`` 를
따로 두는 이유다.

같은 이유로 **localnet 서명에 Solana Explorer URL 을 만들지 않는다.**
``explorer_url`` 은 항상 ``None`` 이고 ``signature_verifiable`` 은 ``False`` 다.
공개 익스플로러는 localnet 을 모르므로, 링크를 걸면 깨진 링크가 아니라 *다른
체인의 엉뚱한 트랜잭션* 을 가리킬 수 있다.

게이트웨이
----------
공식 카탈로그의 Gemini 게이트웨이는 402 챌린지에서 mainnet 만 광고한다. 대회
규정상 mainnet 을 쓸 수 없으므로 결제 **수취 측**을 직접 세운다
(``backend/gateway/paywall.yml``). 업스트림은 진짜 Gemini API 이고, 402 도
USDC 결제도 200 도 전부 실제로 일어난다. 결제 우회가 아니다.

URL 은 하드코딩하지 않는다. ``TREAGENT_PAYSH_GATEWAY_URL`` 하나만 바꾸면
카탈로그 게이트웨이로 되돌아갈 수 있어야 한다.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from treagent.judgment import inference_cost, prompts
from treagent.judgment.models import USDC_DECIMALS, ZERO, coerce_usdc
from treagent.judgment.providers.aistudio import (
    MAX_OUTPUT_TOKENS,
    TEMPERATURE,
    TOP_P,
)
from treagent.judgment.providers.base import ProviderError, ProviderResult

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 설정 — 전부 환경변수. 게이트웨이 URL 하드코딩 금지.
# --------------------------------------------------------------------------- #

DEFAULT_GATEWAY_URL = "http://127.0.0.1:1402"
"""자체 게이트웨이의 기본 바인드 주소. 카탈로그 게이트웨이로 바꾸려면
``TREAGENT_PAYSH_GATEWAY_URL`` 만 교체한다."""

DEFAULT_MODE = "sandbox"
"""``sandbox`` = hosted Surfpool localnet, ``local`` = localhost Surfpool.

**mainnet 은 지원하지 않는다.** 코드 경로 자체를 두지 않는다 — 대회 규정
위반이고, 플래그가 존재하면 언젠가 실수로 켜진다."""

VALID_MODES = frozenset({"sandbox", "local"})

DEFAULT_TIMEOUT_S = 120.0
"""**안전망이지 목표가 아니다.**

few-shot 축소(4개 -> 2개, 8,108 -> 4,194 입력 토큰) 후 정상 왕복은 7~10초다.
그런데 무료 티어 키는 큰 프롬프트에서 간헐적으로 60~120초까지 늘어진다
(실측: 67.7초 / 100.8초 / 119초). 그 구간에서 타임아웃이 나면 폴백으로 떨어져
데모가 죽는다. 정상 경로가 10초인데 120초를 주는 것은 낭비가 아니라, 드물게
느린 꼬리를 폴백으로 오인하지 않기 위한 여유다."""

PAYMENT_NETWORK = "surfpool-localnet"

ACCOUNTS_PATH = Path.home() / ".config" / "pay" / "accounts.yml"
SURFPOOL_RPC = {
    "sandbox": "https://402.surfnet.dev:8899",
    "local": "http://localhost:8899",
}
USDC_MINT_LOCALNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def gateway_url() -> str:
    return os.getenv("TREAGENT_PAYSH_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")


def payment_mode() -> str:
    mode = os.getenv("TREAGENT_PAYSH_MODE", DEFAULT_MODE).strip().lower()
    if mode not in VALID_MODES:
        raise ProviderError(
            f"TREAGENT_PAYSH_MODE 가 {mode!r} 이다. "
            f"허용값은 {sorted(VALID_MODES)} 뿐이다 (mainnet 은 규정상 불가)."
        )
    return mode


def timeout_seconds() -> float:
    return float(os.getenv("TREAGENT_PAYSH_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))


def model_name() -> str:
    from treagent.judgment.providers.aistudio import DEFAULT_MODEL

    return DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# 예외
# --------------------------------------------------------------------------- #


class InsufficientInferenceBudget(ProviderError):
    """추론 예산이 정책 하한 아래다. **호출하지 않았다.**

    ``ProviderError`` 를 상속하는 것은 의도다. 이 예외를 특별히 다루지 않는
    호출부에서도 결론은 같기 때문이다 — 폴백으로 간다. 다만 엔진은 이것만
    따로 잡아 "Vault -> Operations 긴급 충전" 을 최우선 액션으로 삽입한 뒤
    재시도한다 (:meth:`~treagent.judgment.engine.JudgmentEngine._obtain_plan`).
    """

    def __init__(
        self, remaining_days: Decimal, required_days: Decimal, balance_usdc: Decimal
    ) -> None:
        self.remaining_days = remaining_days
        self.required_days = required_days
        self.balance_usdc = balance_usdc
        super().__init__(
            f"추론 예산 잔여 {remaining_days}일 < 정책 하한 {required_days}일 "
            f"(sandbox 잔고 {balance_usdc} USDC). 호출하지 않았다."
        )


# --------------------------------------------------------------------------- #
# 스냅샷 — 데모 출력과 감사에 그대로 실린다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PayShSnapshot:
    """x402 호출 한 번의 전모.

    ``sandbox_*`` 와 ``devnet_ops_balance`` 가 **다른 네트워크**라는 점이
    이 구조체의 존재 이유다. 한 필드에 합치면 "Devnet Operations Wallet 에서
    추론비가 빠져나갔다" 는 거짓 서사가 만들어진다.
    """

    provider: str = "paysh"
    gateway_type: str = "self-hosted"
    payment_mode: str = DEFAULT_MODE
    payment_network: str = PAYMENT_NETWORK
    gateway_url: str = ""
    model: str = ""

    challenge_status: int | None = None
    response_status: int | None = None
    payment_signature: str | None = None
    payment_receipt: dict[str, Any] | None = None

    signature_verifiable: bool = False
    """공개 익스플로러로 검증 가능한가. localnet 이므로 언제나 ``False``."""

    explorer_url: str | None = None
    """localnet 서명에는 **절대** 익스플로러 URL 을 만들지 않는다. 자금 집행
    (Devnet TRANSFER) 만 링크를 갖는다."""

    cost_usdc: Decimal | None = None
    """**이번 한 번의 실측 청구액.** 402 챌린지가 고지하고 온체인으로 나간 금액이다."""

    assumed_unit_cost_usdc: Decimal | None = None
    """예산 계산에 쓰는 **가정** 단가. ``cost_usdc`` 와 절대 섞지 않는다."""

    projected_daily_calls: int | None = None
    """하루 예상 호출 수 (게이팅 기준). 가정치다."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    degraded: bool = False
    degraded_reason: str | None = None

    # 잔고 — 서로 다른 네트워크다.
    sandbox_balance_before: Decimal | None = None
    sandbox_balance_after: Decimal | None = None
    devnet_ops_balance: Decimal | None = None
    projected_ops_balance: Decimal | None = None
    estimated_runway_days: Decimal | None = None
    booked_as_obligation_id: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """``ProviderResult.metadata`` 에 실을 평평한 dict."""

        def dec(value: Decimal | None) -> str | None:
            return None if value is None else format(value, "f")

        return {
            "provider": self.provider,
            "gateway_type": self.gateway_type,
            "payment_mode": self.payment_mode,
            "payment_network": self.payment_network,
            "gateway_url": self.gateway_url,
            "model": self.model,
            "challenge_status": self.challenge_status,
            "response_status": self.response_status,
            "payment_signature": self.payment_signature,
            "payment_receipt": self.payment_receipt,
            "signature_verifiable": self.signature_verifiable,
            "explorer_url": self.explorer_url,
            "cost_usdc": dec(self.cost_usdc),
            "assumed_unit_cost_usdc": dec(self.assumed_unit_cost_usdc),
            "projected_daily_calls": self.projected_daily_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "sandbox_balance_before": dec(self.sandbox_balance_before),
            "sandbox_balance_after": dec(self.sandbox_balance_after),
            "devnet_ops_balance": dec(self.devnet_ops_balance),
            "projected_ops_balance": dec(self.projected_ops_balance),
            "estimated_runway_days": dec(self.estimated_runway_days),
            "booked_as_obligation_id": self.booked_as_obligation_id,
        }


# --------------------------------------------------------------------------- #
# HTTP 응답 파싱 — 정규식으로 stdout 을 긁지 않는다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HttpResponse:
    """``curl -i`` 출력에서 뽑아낸 마지막 응답."""

    status: int
    headers: dict[str, str]
    body: str


def parse_http_responses(raw: str) -> list[HttpResponse]:
    """``curl -i`` 의 stdout 을 응답 목록으로 자른다.

    ``pay`` 가 402 를 받고 재시도하면 응답이 두 벌 쌓일 수 있으므로 전부
    돌려준다. 헤더 이름은 소문자로 정규화한다 (HTTP 헤더는 대소문자 무시).
    """
    text = raw.replace("\r\n", "\n")
    responses: list[HttpResponse] = []
    index = 0
    while True:
        start = text.find("HTTP/", index)
        if start < 0:
            break
        split = text.find("\n\n", start)
        if split < 0:
            head, body_start = text[start:], len(text)
        else:
            head, body_start = text[start:split], split + 2

        lines = head.split("\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError):
            index = start + 5
            continue

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        next_start = text.find("HTTP/", body_start)
        body = text[body_start : next_start if next_start > 0 else len(text)]
        responses.append(HttpResponse(status=status, headers=headers, body=body))
        index = next_start if next_start > 0 else len(text)
        if next_start < 0:
            break
    return responses


def _b64_json(blob: str) -> dict[str, Any] | None:
    """base64url(JSON) 을 푼다. 실패하면 ``None`` — 치명적이지 않다."""
    try:
        padded = blob + "=" * (-len(blob) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def parse_payment_signature(
    response: HttpResponse,
) -> tuple[str | None, dict[str, Any] | None]:
    """``payment-receipt`` 헤더에서 결제 서명을 뽑는다.

    정규식으로 stdout 을 긁지 않는다. 영수증은 base64url(JSON) 이고 그 안의
    ``reference`` 가 Solana 트랜잭션 서명이다 — 구조를 아는 파싱이다.

    **서명이 없어도 치명적이지 않다.** 판단은 이미 성공했다. ``None`` 을
    돌려주고 경고만 남긴다.
    """
    receipt_raw = response.headers.get("payment-receipt")
    if not receipt_raw:
        logger.warning("payment-receipt 헤더가 없다 — payment_signature 는 None 이다.")
        return None, None

    receipt = _b64_json(receipt_raw)
    if receipt is None:
        logger.warning("payment-receipt 를 디코드하지 못했다 — 서명은 None 이다.")
        return None, None

    reference = receipt.get("reference")
    if not isinstance(reference, str) or not reference:
        logger.warning("payment-receipt 에 reference 가 없다 — 서명은 None 이다.")
        return None, receipt
    if receipt.get("status") != "success":
        logger.warning("payment-receipt status=%r — 서명을 신뢰하지 않는다.", receipt.get("status"))
    return reference, receipt


def parse_challenge_amount(response: HttpResponse) -> Decimal | None:
    """402 챌린지에서 **실제 청구 금액**을 뽑는다.

    추정치가 아니다. ``www-authenticate: Payment ... request="<base64>"`` 안의
    ``amount`` 는 base unit 정수이고, 이 금액이 그대로 온체인
    ``transferChecked`` 로 나가는 것을 트랜잭션으로 확인했다.
    """
    header = response.headers.get("www-authenticate")
    if not header or "request=" not in header:
        return None
    blob = header.split('request="', 1)[-1].split('"', 1)[0]
    challenge = _b64_json(blob)
    if challenge is None:
        return None
    amount = challenge.get("amount")
    if amount is None:
        return None
    details = challenge.get("methodDetails") or {}
    decimals = int(details.get("decimals", USDC_DECIMALS))
    try:
        return coerce_usdc(str(amount)).scaleb(-decimals)
    except TypeError:
        return None


# --------------------------------------------------------------------------- #
# sandbox 지갑 잔고 — Devnet 과 무관하다
# --------------------------------------------------------------------------- #


def sandbox_payer_address(mode: str = DEFAULT_MODE) -> str | None:
    """``~/.config/pay/accounts.yml`` 의 localnet 기본 지갑 주소.

    YAML 파서를 끌어오지 않는다. 필요한 것은 ``localnet.default.pubkey`` 한
    줄뿐이고, 이 파일 형식을 위해 의존성을 늘릴 이유가 없다.
    """
    if not ACCOUNTS_PATH.is_file():
        return None
    in_localnet = in_default = False
    for line in ACCOUNTS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("localnet:"):
            in_localnet, in_default = True, False
            continue
        if in_localnet and indent <= 2 and stripped.endswith(":"):
            in_localnet = False
        if in_localnet and stripped.startswith("default:"):
            in_default = True
            continue
        if in_default and stripped.startswith("pubkey:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
        if in_default and indent <= 4 and stripped.endswith(":"):
            in_default = False
    return None


def sandbox_usdc_balance(mode: str = DEFAULT_MODE, address: str | None = None) -> Decimal | None:
    """Surfpool localnet 의 USDC 잔고. 조회 실패는 ``None`` (거부권 없음).

    **이 값은 Devnet Operations Wallet 잔고가 아니다.** 별도 네트워크다.
    """
    import urllib.error
    import urllib.request

    payer = address or sandbox_payer_address(mode)
    if payer is None:
        return None
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                payer,
                {"mint": USDC_MINT_LOCALNET},
                {"encoding": "jsonParsed"},
            ],
        }
    ).encode()
    request = urllib.request.Request(
        SURFPOOL_RPC[mode], data=payload, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("sandbox 잔고 조회 실패: %s", exc)
        return None

    accounts = (body.get("result") or {}).get("value") or []
    if not accounts:
        return ZERO
    amount = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
    return coerce_usdc(str(amount["uiAmountString"]))


# --------------------------------------------------------------------------- #
# 공급자
# --------------------------------------------------------------------------- #


@dataclass
class PayShGeminiProvider:
    """``pay`` CLI 를 통해 x402 로 결제하고 Gemini 를 부른다.

    **AIStudioGeminiProvider 를 생성하지도 호출하지도 않는다.** 공급자가 다른
    공급자를 알면 순환 의존과 강결합이 생긴다. 폴백 순서는 엔진이 정한다.
    """

    name: str = "paysh"
    model: str = field(default_factory=model_name)
    mode: str = field(default_factory=payment_mode)
    gateway: str = field(default_factory=gateway_url)
    timeout_s: float = field(default_factory=timeout_seconds)
    check_budget: bool = True
    inference_budget_min_days: Decimal | None = None
    """정책 하한. ``None`` 이면 예산 검사를 건너뛴다."""

    devnet_ops_balance: Decimal | None = None
    """Devnet Operations Wallet 잔고. 표시 전용이며 **차감되지 않는다.**"""

    last_snapshot: PayShSnapshot | None = field(default=None, init=False)

    # ------------------------------------------------------------------ #

    @property
    def endpoint(self) -> str:
        return f"{self.gateway}/v1beta/models/{self.model}:generateContent"

    def _request_body(self, system: str, user: str, schema: dict[str, Any]) -> str:
        contents: list[dict[str, Any]] = []
        for example_user, example_assistant in prompts.load_examples():
            contents.append({"role": "user", "parts": [{"text": example_user}]})
            contents.append({"role": "model", "parts": [{"text": example_assistant}]})
        contents.append({"role": "user", "parts": [{"text": user}]})
        return json.dumps(
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {
                    "temperature": TEMPERATURE,
                    "topP": TOP_P,
                    "maxOutputTokens": MAX_OUTPUT_TOKENS,
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                    # Gemini 3.x 는 사고 토큰을 기본으로 켜고, 그 토큰이
                    # maxOutputTokens 예산을 함께 먹는다. 끄지 않으면 사고에
                    # 2,000 토큰을 쓰고 본문이 67 토큰에서 잘려 JSON 이 깨진다
                    # (실측). 이 판단에는 사고가 필요 없다 — 산술은 이미 끝났고
                    # 모델이 하는 일은 정렬과 타이밍 선택뿐이다.
                    "thinkingConfig": {"thinkingLevel": "low"},
                },
            },
            ensure_ascii=False,
        )

    def _run(self, args: list[str], payload: str, timeout_s: float) -> str:
        command = [
            "pay",
            "--no-dna",
            f"--{self.mode}",
            "curl",
            "-i",
            *args,
            self.endpoint,
            "-H",
            "content-type: application/json",
            "-d",
            payload,
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                f"pay CLI 가 {timeout_s}초 안에 응답하지 않았다."
            ) from exc
        if completed.returncode != 0 and not completed.stdout:
            raise ProviderError(
                f"pay CLI 실패 (exit {completed.returncode}): "
                f"{completed.stderr.strip()[:400]}"
            )
        return completed.stdout

    def probe_challenge(self, payload: str) -> tuple[int | None, Decimal | None]:
        """결제 없이 한 번 쳐서 402 챌린지와 청구 금액을 확인한다.

        ``pay`` 를 거치지 않는 맨 HTTP 요청이므로 돈이 나가지 않는다. 데모가
        ``402 Payment Required`` 를 화면에 띄울 근거이자, ``cost_usdc`` 를
        추정이 아닌 **고지된 청구액**으로 채우는 출처다.
        """
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            self.endpoint,
            data=payload.encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, None
        except urllib.error.HTTPError as exc:
            headers = {k.lower(): v for k, v in exc.headers.items()}
            probe = HttpResponse(status=exc.code, headers=headers, body="")
            return exc.code, parse_challenge_amount(probe)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"게이트웨이에 닿지 못했다: {exc}") from exc

    def _enforce_budget(self, balance: Decimal | None, cost: Decimal | None) -> None:
        """추론 예산 거부권. 호출 **전에** 판단한다.

        잔고나 단가를 모르면 거부권을 행사하지 않는다. 모른다는 이유로 판단을
        멈추면 급여가 밀리고, 그건 예산 초과보다 나쁜 결과다.
        """
        if not self.check_budget or self.inference_budget_min_days is None:
            return
        if balance is None or cost is None or cost <= ZERO:
            return
        remaining_days = balance / cost
        if remaining_days < self.inference_budget_min_days:
            raise InsufficientInferenceBudget(
                remaining_days=remaining_days.quantize(Decimal("0.01")),
                required_days=self.inference_budget_min_days,
                balance_usdc=balance,
            )

    def generate_plan(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> ProviderResult:
        """402 -> 결제 -> 200. 실패하면 예외 — 폴백 순서는 엔진이 정한다."""
        budget = max(timeout_s, self.timeout_s)
        payload = self._request_body(system, user, schema)

        challenge_status, quoted_cost = self.probe_challenge(payload)
        balance_before = sandbox_usdc_balance(self.mode)
        self._enforce_budget(balance_before, quoted_cost)

        started = time.monotonic()
        raw = self._run([], payload, budget)
        latency_ms = int((time.monotonic() - started) * 1000)

        responses = parse_http_responses(raw)
        if not responses:
            raise ProviderError(f"pay 출력에서 HTTP 응답을 찾지 못했다: {raw[:300]!r}")
        final = responses[-1]
        if final.status != 200:
            raise ProviderError(
                f"게이트웨이가 {final.status} 를 냈다: {final.body[:300]}"
            )

        signature, receipt = parse_payment_signature(final)
        balance_after = sandbox_usdc_balance(self.mode)
        text, usage = _extract_gemini_text(final.body)

        cost = quoted_cost
        if cost is None and balance_before is not None and balance_after is not None:
            # 챌린지를 못 읽었을 때의 대체 출처. 여전히 추정이 아니라 실측이다.
            delta = balance_before - balance_after
            cost = delta if delta > ZERO else None

        snapshot = PayShSnapshot(
            payment_mode=self.mode,
            gateway_url=self.gateway,
            model=self.model,
            challenge_status=challenge_status,
            response_status=final.status,
            payment_signature=signature,
            payment_receipt=receipt,
            signature_verifiable=False,
            explorer_url=None,
            cost_usdc=cost,
            assumed_unit_cost_usdc=inference_cost.assumed_unit_cost_usdc(),
            projected_daily_calls=inference_cost.PROJECTED_DAILY_CALLS,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            latency_ms=latency_ms,
            degraded=signature is None,
            degraded_reason=None if signature else "payment_signature 를 파싱하지 못했다.",
            sandbox_balance_before=balance_before,
            sandbox_balance_after=balance_after,
            devnet_ops_balance=self.devnet_ops_balance,
            projected_ops_balance=(
                self.devnet_ops_balance - cost
                if self.devnet_ops_balance is not None and cost is not None
                else None
            ),
            # **일수이지 호출 수가 아니다.** 가정 단가 x 일일 호출수로 나눈다.
            estimated_runway_days=inference_cost.estimated_runway_days(balance_after),
        )
        self.last_snapshot = snapshot

        return ProviderResult(
            raw_text=text,
            input_tokens=snapshot.input_tokens,
            output_tokens=snapshot.output_tokens,
            latency_ms=latency_ms,
            payment_signature=signature,
            cost_usdc=cost,
            degraded=snapshot.degraded,
            metadata=snapshot.to_metadata(),
        )


def _extract_gemini_text(body: str) -> tuple[str, dict[str, Any]]:
    """generateContent 응답에서 본문 텍스트와 토큰 사용량을 꺼낸다."""
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise ProviderError(f"게이트웨이 응답이 JSON 이 아니다: {body[:300]!r}") from exc

    candidates = parsed.get("candidates") or []
    if not candidates:
        raise ProviderError(f"응답에 candidates 가 없다: {body[:300]!r}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise ProviderError("Gemini 가 빈 응답을 냈다.")
    usage = parsed.get("usageMetadata") or {}
    return text, usage


__all__ = [
    "DEFAULT_GATEWAY_URL",
    "DEFAULT_TIMEOUT_S",
    "PAYMENT_NETWORK",
    "HttpResponse",
    "InsufficientInferenceBudget",
    "PayShGeminiProvider",
    "PayShSnapshot",
    "gateway_url",
    "parse_challenge_amount",
    "parse_http_responses",
    "parse_payment_signature",
    "payment_mode",
    "sandbox_payer_address",
    "sandbox_usdc_balance",
]
