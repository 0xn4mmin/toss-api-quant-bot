"""mkt.* — 시장 상태 조회 표면 (ARCH-06: index/fx/hours/screener).

레짐 필터(index·VIX)·KR 스크리너 유니버스·환율·장 시간 판단의 입력이다.
"""

from __future__ import annotations

from quantbot.adapter.contracts import (
    FxRate,
    IndexQuote,
    MarketHours,
    ScreenerResult,
    call,
)
from quantbot.adapter.proc import TossctlRunner


def index(runner: TossctlRunner, name: str) -> IndexQuote:
    return call(runner, ["market", "index", name], IndexQuote)


def fx(runner: TossctlRunner, pair: str) -> FxRate:
    return call(runner, ["market", "fx", pair], FxRate)


def hours(runner: TossctlRunner, market: str) -> MarketHours:
    return call(runner, ["market", "hours", market], MarketHours)


def screener(runner: TossctlRunner, preset: str) -> ScreenerResult:
    return call(runner, ["market", "screener", "--preset", preset], ScreenerResult)
