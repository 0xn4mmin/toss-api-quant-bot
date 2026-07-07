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


def exchange_rate(
    client: OpenApiClient, base_currency: str, quote_currency: str
) -> ExchangeRateResponse:
    """환율 조회 — baseCurrency·quoteCurrency는 명세상 필수 (2026-07-07 실측 확정:
    파라미터 없이 호출하면 400 invalid-request)."""
    return call_api(
        client, "/api/v1/exchange-rate", "MARKET_INFO", ExchangeRateResponse,
        params={"baseCurrency": base_currency, "quoteCurrency": quote_currency},
    )


def market_calendar_kr(client: OpenApiClient) -> KrMarketCalendarResponse:
    return call_api(client, "/api/v1/market-calendar/KR", "MARKET_INFO", KrMarketCalendarResponse)


def market_calendar_us(client: OpenApiClient) -> UsMarketCalendarResponse:
    return call_api(client, "/api/v1/market-calendar/US", "MARKET_INFO", UsMarketCalendarResponse)
