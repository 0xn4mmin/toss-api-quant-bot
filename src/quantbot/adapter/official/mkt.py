"""공식 API 시장 정보 표면 (ARCH-06 v1.1: 환율·장 운영 시간).

지수·스크리너는 공식 API에 없다 — adapter.tossctl.mkt이 담당한다.
"""

from __future__ import annotations

from quantbot.adapter.official.contracts import (
    ExchangeRateResponse,
    KrMarketCalendarResponse,
    UsMarketCalendarResponse,
    call_api,
)
from quantbot.adapter.official.http import OpenApiClient


def exchange_rate(client: OpenApiClient) -> ExchangeRateResponse:
    return call_api(client, "/api/v1/exchange-rate", "MARKET_INFO", ExchangeRateResponse)


def market_calendar_kr(client: OpenApiClient) -> KrMarketCalendarResponse:
    return call_api(client, "/api/v1/market-calendar/KR", "MARKET_INFO", KrMarketCalendarResponse)


def market_calendar_us(client: OpenApiClient) -> UsMarketCalendarResponse:
    return call_api(client, "/api/v1/market-calendar/US", "MARKET_INFO", UsMarketCalendarResponse)
