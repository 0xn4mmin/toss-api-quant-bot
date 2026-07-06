"""Broker 프로토콜 (IMPL-03) — 조회 표면의 공용 인터페이스.

TossctlBroker(실물 조회)와 PaperBroker(주입 상태)가 동일 인터페이스를 구현한다 —
페이퍼트레이딩이 "다른 코드 경로"가 아니라 같은 엔진에 브로커만 바꿔 끼운 것이
되게 하는 장치. 주문 표면(preview/execute)은 Phase 4에서 GATE 전용 타입과 함께
이 프로토콜에 추가된다 — 조회 Phase의 표면에는 주문이 존재하지 않는다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from quantbot.adapter import acct, md
from quantbot.adapter.contracts import Position, Quote
from quantbot.adapter.proc import TossctlRunner


@runtime_checkable
class Broker(Protocol):
    """조회 표면 — 엔진이 아는 유일한 브로커 인터페이스."""

    def cash(self) -> float: ...

    def positions(self) -> list[Position]: ...

    def quote(self, symbol: str) -> Quote: ...


class TossctlBroker:
    """실물 조회 — md/acct 표면을 묶는다."""

    def __init__(self, runner: TossctlRunner) -> None:
        self._runner = runner

    def cash(self) -> float:
        return acct.summary(self._runner).cash

    def positions(self) -> list[Position]:
        return list(acct.positions(self._runner).positions)

    def quote(self, symbol: str) -> Quote:
        return md.quote_get(self._runner, symbol)


class PaperBroker:
    """모의 조회 — 상태를 주입받는다. 체결 모델은 Phase 4에서 backtest.sim과
    동일 계보를 공유하도록 추가된다 (§I3)."""

    def __init__(
        self,
        cash: float,
        positions: list[Position],
        quotes: dict[str, Quote],
    ) -> None:
        self._cash = cash
        self._positions = list(positions)
        self._quotes = dict(quotes)

    def cash(self) -> float:
        return self._cash

    def positions(self) -> list[Position]:
        return list(self._positions)

    def quote(self, symbol: str) -> Quote:
        if symbol not in self._quotes:
            raise KeyError(f"모의 시세 없음: {symbol}")
        return self._quotes[symbol]
