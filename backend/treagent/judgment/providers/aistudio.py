"""AI Studio API 키로 Gemini 를 부르는 공급자 — **폴백 경로다.**

기본 경로는 :mod:`~treagent.judgment.providers.paysh` (x402 결제)다. 이 모듈은
게이트웨이가 죽었거나 결제가 안 될 때를 위한 대비책이며, 그와 별개로
**프롬프트 자체를 결제 변수 없이 검증하는 용도**로도 쓴다.

이 공급자가 하지 않는 것
------------------------
검증을 하지 않는다. ``raw_text`` 를 돌려줄 뿐이고, 그 문자열이
:class:`~treagent.judgment.models.DecisionPlan` 인지, 금액이 조작됐는지,
지갑이 실재하는지는 전부 :class:`~treagent.judgment.guard.PolicyGuard` 가 본다.

폴백 순서(paysh -> aistudio -> FallbackPlan)도 여기서 정하지 않는다. 공급자가
다른 공급자를 알면 순환 의존이 생긴다 — 순서는
:class:`~treagent.judgment.engine.JudgmentEngine` 이 관리한다.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from treagent.judgment import prompts
from treagent.judgment.providers.base import ProviderError, ProviderResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("TREAGENT_GEMINI_MODEL", "gemini-3.5-flash")
"""Pro 를 쓰지 않는다. 이 판단에는 산술도 장문 추론도 없다 — 부족분은 이미
Python 이 계산했고 모델이 하는 일은 정렬과 타이밍 선택뿐이다. Pro 는 비용과
지연만 늘린다.

원래 ``gemini-2.5-flash`` 를 지정했으나 신규 API 키에는 404 가 돌아온다
("no longer available to new users"). 같은 flash 티어에서 실측으로 고른
대체가 ``gemini-3.5-flash`` 다 (약 6초, 출력 550토큰).
``gemini-flash-latest`` 같은 별칭은 쓰지 않는다 — 데모 재현성이 모델 교체에
휘둘리면 안 된다. 환경변수로만 바꾼다."""

TEMPERATURE = 0.0
TOP_P = 1.0
MAX_OUTPUT_TOKENS = 2048
RESPONSE_MIME_TYPE = "application/json"

RETRY_NOTICE_KO = (
    "직전 응답이 스키마 검증에 실패했다. 아래 오류를 고쳐 **JSON 객체 하나만** "
    "다시 출력하라. 마크다운 코드펜스도 설명 문장도 붙이지 마라.\n\n"
    "오류:\n{error}"
)
"""재시도 프롬프트. 원래 지시를 반복하지 않고 **차이만** 준다. 전체를 다시
보내면 모델이 첫 응답을 그대로 되풀이하는 경향이 있다."""


def build_contents(types: Any, user: str, retry_error: str | None) -> list[Any]:
    """few-shot 을 멀티턴으로 깔고 이번 입력을 마지막에 붙인다.

    예시를 유저 메시지에 이어 붙이지 않는 이유는 신뢰 경계 때문이다. 예시도
    ``<untrusted_obligations>`` 를 포함하므로 한 덩어리로 합치면 모델 입장에서
    '이번에 처리할 입력' 과 구분이 사라진다.

    ``types`` 를 인자로 받는 것은 ``google.genai`` 를 모듈 최상단에서 import
    하지 않기 위해서다. SDK 가 없는 환경에서도 이 모듈은 import 되어야 한다
    (테스트가 ``TREAGENT_LLM_PROVIDER=mock`` 으로 도는 CI 가 그렇다).
    """

    def turn(role: str, text: str) -> Any:
        return types.Content(role=role, parts=[types.Part(text=text)])

    contents: list[Any] = []
    for example_user, example_assistant in prompts.load_examples():
        contents.append(turn("user", example_user))
        contents.append(turn("model", example_assistant))
    contents.append(turn("user", user))
    if retry_error is not None:
        contents.append(turn("user", RETRY_NOTICE_KO.format(error=retry_error)))
    return contents


@dataclass
class AIStudioGeminiProvider:
    """``google-genai`` SDK + ``GEMINI_API_KEY``.

    ``client`` 를 주입하면 그것을 쓴다 (테스트용). 비워두면 첫 호출 때 만든다 —
    import 시점에 만들면 키가 없는 환경에서 모듈 import 만으로 터진다.
    """

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    name: str = "aistudio"
    client: Any | None = None
    last_error: str | None = field(default=None, init=False)
    """직전 호출에서 SDK 가 낸 오류. 재시도 프롬프트가 아니라 디버깅용이다."""

    # ------------------------------------------------------------------ #

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client

        key = self.api_key or os.getenv("GEMINI_API_KEY") or ""
        if not key:
            raise ProviderError(
                "GEMINI_API_KEY 가 없다. .env 에 채우거나 "
                "AIStudioGeminiProvider(api_key=...) 로 넘겨라."
            )
        try:
            from google.genai import Client as GenaiClient
        except ImportError as exc:  # pragma: no cover - 설치 안 된 환경
            raise ProviderError(
                "google-genai 가 설치돼 있지 않다: pip install google-genai"
            ) from exc

        self.client = GenaiClient(api_key=key)
        return self.client

    def generate_plan(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: float = 10.0,
    ) -> ProviderResult:
        """한 번 호출한다. 재시도는 :meth:`generate_plan_with_retry` 가 한다."""
        return self._call(system, user, schema, timeout_s, retry_error=None)

    def generate_plan_with_retry(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: float = 10.0,
        *,
        validate: Any = None,
    ) -> ProviderResult:
        """파싱 실패 시 오류를 첨부해 **1회만** 재시도한다.

        ``validate`` 는 ``raw_text -> DecisionPlan | None`` 인 콜러블이다
        (보통 ``PolicyGuard.parse_plan``). ``None`` 이면 재시도하지 않는다 —
        검증 책임을 공급자가 지지 않기 위해서다.

        두 번으로 고정한 이유는 engine 의 ``SCHEMA_RETRIES`` 와 같다. 두 번
        틀린 모델은 세 번째에도 틀리고, 그동안 잔고는 계속 움직인다.
        """
        result = self._call(system, user, schema, timeout_s, retry_error=None)
        if validate is None or validate(result.raw_text) is not None:
            return result

        logger.warning("aistudio: 스키마 파싱 실패 — 오류를 첨부해 1회 재시도한다.")
        retry = self._call(
            system,
            user,
            schema,
            timeout_s,
            retry_error="DecisionPlan 스키마 검증에 실패했다. "
            "스키마에 없는 필드를 추가하지 말고, 필수 필드를 빠뜨리지 마라.",
        )
        if validate(retry.raw_text) is None:
            raise ProviderError(
                "aistudio 가 두 번 연속 DecisionPlan 스키마를 만족하지 못했다."
            )
        return retry

    # ------------------------------------------------------------------ #

    def _call(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: float,
        *,
        retry_error: str | None,
    ) -> ProviderResult:
        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type=RESPONSE_MIME_TYPE,
            # schema 는 engine 이 sanitize_schema_for_gemini() 를 통과시켜 넘긴다.
            # 여기서 다시 손대면 정규화 규칙이 두 군데로 갈라진다.
            response_schema=schema,
            # SDK 는 밀리초를 받는다. engine 의 timeout_s 와 단위가 다르다.
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )

        started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=build_contents(types, user, retry_error),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - SDK 예외 종류가 많고 결론은 같다
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise ProviderError(f"Gemini 호출 실패: {self.last_error}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        text = (response.text or "").strip()
        if not text:
            # 빈 응답은 보통 safety 차단이거나 max_output_tokens 초과다.
            # 둘 다 폴백으로 가야 하므로 여기서 구분하지 않는다.
            raise ProviderError(
                f"Gemini 가 빈 응답을 냈다 (finish_reason 확인 필요, {latency_ms}ms)."
            )

        usage = getattr(response, "usage_metadata", None)
        return ProviderResult(
            raw_text=text,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            latency_ms=latency_ms,
            # API 키 경로에는 온체인 결제가 없다. 이 두 칸이 비어 있다는 것
            # 자체가 "x402 를 타지 않았다" 는 신호다.
            payment_signature=None,
            cost_usdc=None,
            degraded=False,
            metadata={
                "model": self.model,
                "route": "aistudio_api_key",
                "retry": retry_error is not None,
            },
        )


__all__ = ["DEFAULT_MODEL", "MAX_OUTPUT_TOKENS", "AIStudioGeminiProvider"]
