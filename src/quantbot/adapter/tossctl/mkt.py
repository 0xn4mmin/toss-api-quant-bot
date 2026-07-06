"""지수·스크리너 조회 표면 — 공식 API 미제공분 (ARCH-06 v1.1).

지수는 레짐 필터(§S4)의 입력이고, 스크리너는 KR 유니버스 갱신의 원천이다.
"""

from __future__ import annotations

from quantbot.adapter.tossctl.contracts import IndexQuote, ScreenerResult, call
from quantbot.adapter.tossctl.proc import TossctlRunner


def index(runner: TossctlRunner, name: str) -> IndexQuote:
    return call(runner, ["market", "index", name], IndexQuote)


def screener(runner: TossctlRunner, preset: str) -> ScreenerResult:
    return call(runner, ["market", "screener", "--preset", preset], ScreenerResult)
