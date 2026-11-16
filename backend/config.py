"""애플리케이션 설정.

온체인 관련 상수는 chain.client 가 소유한다. 여기에는 그 바깥에서 필요한
운영 스위치만 둔다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent

for _candidate in (_REPO_ROOT / ".env", _BACKEND_DIR / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=True)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().strip("'\"").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """런타임 설정 스냅샷."""

    MOCK_MODE: bool
    """True 면 services 레이어가 온체인 대신 mock_data 를 반환한다.

    시연 도중 devnet RPC 가 죽거나 레이트리밋에 걸렸을 때 즉시 되돌아갈 수 있도록
    남겨둔 폴백이다. 서버 재시작 없이 바꾸려면 refresh() 를 쓴다.
    """

    GEMINI_API_KEY: str

    @classmethod
    def load(cls) -> Settings:
        return cls(
            MOCK_MODE=_env_bool("MOCK_MODE", default=False),
            GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", "").strip().strip("'\""),
        )


settings = Settings.load()


def refresh() -> Settings:
    """환경변수를 다시 읽어 settings 를 갱신한다 (시연 중 모드 전환용)."""
    global settings
    settings = Settings.load()
    return settings
