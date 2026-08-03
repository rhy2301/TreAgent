"""LLM 공급자.

이번 세션에는 :mod:`~treagent.judgment.providers.mock` 하나뿐이다.

TODO(session-B): ``aistudio.py`` — Gemini API 연동.
TODO(session-B): ``paysh.py`` — x402 결제 경유 추론.
둘 다 :class:`~treagent.judgment.providers.base.LLMProvider` 만 만족하면
엔진·가드는 손대지 않는다.
"""

from treagent.judgment.providers.base import (
    LLMProvider,
    ProviderError,
    ProviderResult,
)

__all__ = ["LLMProvider", "ProviderError", "ProviderResult"]
