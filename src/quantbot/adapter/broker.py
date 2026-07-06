"""Broker 프로토콜 (IMPL-03 v1.1) — 조회 표면의 공용 인터페이스.

OfficialBroker(공식 API 실물 조회)와 PaperBroker(주입 상태)가 동일 인터페이스를
구현한다 — 페이퍼트레이딩이 "다른 코드 경로"가 아니라 같은 엔진에 브로커만 바꿔
끼운 것이 되게 하는 장치. 주문 표면(preview/execute)은 Phase 4에서 GATE 전용
타입과 함께 이 프로토콜에 추가된다 — 조회 Phase의 표면에는 주문이 존재하지 않는다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from quantbot.adapter.official import acct, md, tradeinfo
from quantbot.adapter.official.contracts import HoldingsItem, PriceResponse
from quantbot.adapter.official.http import OpenApiClient


@runtime_checkable
class Broker(Protocol):
    """조회 표면 — 엔진이 아는 유일한 브로커 인터페이스."""

    def cash(self, currency: str) -> float: ...

    def positions(self) -> list[HoldingsItem]: ...

    def quote(self, symbol: str) -> PriceResponse: ...


class OfficialBroker:
    """공식 API 실물 조회 — md/acct/tradeinfo 표면을 묶는다."""

    def __init__(self, client: OpenApiClient) -> None:
        self._client = client

    def cash(self, currency: str) -> float:
        return float(tradeinfo.buying_power(self._client, currency).cashBuyingPower)

    def positions(self) -> list[HoldingsItem]:
        return list(acct.holdings(self._client).items)

    def quote(self, symbol: str) -> PriceResponse:
        result = md.prices(self._client, [symbol])
        if len(result) != 1 or result[0].symbol != symbol:
            raise KeyError(f"시세 응답이 요청 종목과 다르다: {symbol} → {result}")
        return result[0]


class PaperBroker:
    """모의 조회 — 상태를 주입받는다. 체결 모델은 Phase 4에서 backtest.sim과
    동일 계보를 공유하도록 추가된다 (§I3)."""

    def __init__(
        self,
        cash_by_currency: dict[str, float],
        positions: list[HoldingsItem],
        quotes: dict[str, PriceResponse],
    ) -> None:
        self._cash = dict(cash_by_currency)
        self._positions = list(positions)
        self._quotes = dict(quotes)

    def cash(self, currency: str) -> float:
        if currency not in self._cash:
            raise KeyError(f"모의 잔고 없음: {currency}")
        return self._cash[currency]

    def positions(self) -> list[HoldingsItem]:
        return list(self._positions)

    def quote(self, symbol: str) -> PriceResponse:
        if symbol not in self._quotes:
            raise KeyError(f"모의 시세 없음: {symbol}")
        return self._quotes[symbol]
