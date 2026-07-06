"""공식 API 거래 가능 정보 표면 (ARCH-06 v1.1: Order Info — 조회 전용).

GATE preview 합성의 입력이다 (IMPL §I3 v1.1): 매수 가능 금액·판매 가능 수량·
실측 수수료. 주문 발행과는 무관하다.
"""

from __future__ import annotations

from quantbot.adapter.official.contracts import (
    BuyingPowerResponse,
    Commission,
    SellableQuantityResponse,
    call_api,
)
from quantbot.adapter.official.http import OpenApiClient


def buying_power(client: OpenApiClient, currency: str) -> BuyingPowerResponse:
    return call_api(
        client, "/api/v1/buying-power", "ORDER_INFO", BuyingPowerResponse,
        params={"currency": currency}, with_account=True,
    )


def sellable_quantity(client: OpenApiClient, symbol: str) -> SellableQuantityResponse:
    return call_api(
        client, "/api/v1/sellable-quantity", "ORDER_INFO", SellableQuantityResponse,
        params={"symbol": symbol}, with_account=True,
    )


def commissions(client: OpenApiClient) -> list[Commission]:
    """실측 수수료 — 백테스트 비용 모델(BT-05)의 입력."""
    return call_api(
        client, "/api/v1/commissions", "ORDER_INFO", Commission, many=True,
        with_account=True,
    )
