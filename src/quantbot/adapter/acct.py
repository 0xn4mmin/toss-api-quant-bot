"""acct.* — 계좌 조회 표면 (ARCH-06: account summary · portfolio positions)."""

from __future__ import annotations

from quantbot.adapter.contracts import AccountSummary, Positions, call
from quantbot.adapter.proc import TossctlRunner


def summary(runner: TossctlRunner) -> AccountSummary:
    return call(runner, ["account", "summary"], AccountSummary)


def positions(runner: TossctlRunner) -> Positions:
    return call(runner, ["portfolio", "positions"], Positions)
