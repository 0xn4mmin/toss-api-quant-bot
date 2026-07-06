"""공식 API 시세·종목 조회 표면 (ARCH-06 v1.1: Market Data · Stock Info).

BT-D2 수정주가 검증 포함 — 공식 candles의 adjusted 파라미터로 true/false 두
시리즈를 실측 비교해 조정 여부를 판정한다 (§S7 v1.2).
"""

from __future__ import annotations

from quantbot.adapter.official.contracts import (
    Candle,
    CandlePageResponse,
    OrderbookResponse,
    PriceLimitResponse,
    PriceResponse,
    StockInfo,
    StockWarning,
    Trade,
    call_api,
)
from quantbot.adapter.official.http import OpenApiClient


def prices(client: OpenApiClient, symbols: list[str]) -> list[PriceResponse]:
    return call_api(
        client, "/api/v1/prices", "MARKET_DATA", PriceResponse,
        params={"symbols": ",".join(symbols)}, many=True,
    )


def orderbook(client: OpenApiClient, symbol: str) -> OrderbookResponse:
    return call_api(
        client, "/api/v1/orderbook", "MARKET_DATA", OrderbookResponse,
        params={"symbol": symbol},
    )


def trades(client: OpenApiClient, symbol: str) -> list[Trade]:
    return call_api(
        client, "/api/v1/trades", "MARKET_DATA", Trade,
        params={"symbol": symbol}, many=True,
    )


def price_limits(client: OpenApiClient, symbol: str) -> PriceLimitResponse:
    return call_api(
        client, "/api/v1/price-limits", "MARKET_DATA", PriceLimitResponse,
        params={"symbol": symbol},
    )


def candles(
    client: OpenApiClient,
    symbol: str,
    interval: str,
    count: int,
    *,
    adjusted: bool = True,       # 백테스트·시그널 적재는 항상 수정주가 (BT-D2)
    before: str | None = None,
) -> CandlePageResponse:
    params = {
        "symbol": symbol,
        "interval": interval,
        "count": str(count),
        "adjusted": "true" if adjusted else "false",
    }
    if before is not None:
        params["before"] = before
    return call_api(
        client, "/api/v1/candles", "MARKET_DATA_CHART", CandlePageResponse,
        params=params,
    )


def stocks(client: OpenApiClient, symbols: list[str]) -> list[StockInfo]:
    """종목 마스터 — INV-11(leverageFactor)·BT-D1(status/delistDate)의 데이터 원천."""
    return call_api(
        client, "/api/v1/stocks", "STOCK", StockInfo,
        params={"symbols": ",".join(symbols)}, many=True,
    )


def warnings(client: OpenApiClient, symbol: str) -> list[StockWarning]:
    return call_api(
        client, f"/api/v1/stocks/{symbol}/warnings", "STOCK", StockWarning, many=True,
    )


# ═══ BT-D2 — 수정주가 검증 (STRAT §S7 v1.2) ═════════════════════════════


def adjusted_price_report(
    adjusted_candles: list[Candle],
    raw_candles: list[Candle],
    split_date: str,
    split_ratio: float,
    daily_move_tolerance: float,
) -> dict:
    """adjusted=true/false 두 시리즈를 분할 이벤트 전후로 비교해 조정 여부를 실측한다.

    기대: raw 시리즈는 분할일에 ~1/ratio 점프, adjusted 시리즈는 연속.
    둘 다 아니면 'inconclusive' — 미해결 시 백테스트 자체가 무효다 (BT-D2).
    """
    if split_ratio <= 1.0:
        raise ValueError(f"분할 비율은 1 초과여야 한다: {split_ratio}")

    def move_at(bars: list[Candle]) -> float:
        dates = [b.timestamp[:10] for b in bars]
        if split_date not in dates:
            raise ValueError(f"시리즈에 분할일 {split_date}가 없다")
        i = dates.index(split_date)
        if i == 0:
            raise ValueError("분할일 이전 봉이 없다 — 판정 불가")
        return float(bars[i].closePrice) / float(bars[i - 1].closePrice)

    adj_move, raw_move = move_at(adjusted_candles), move_at(raw_candles)
    expected_jump = 1.0 / split_ratio
    raw_shows_split = abs(raw_move - expected_jump) <= expected_jump * daily_move_tolerance
    adj_continuous = abs(adj_move - 1.0) <= daily_move_tolerance
    if raw_shows_split and adj_continuous:
        verdict = "adjusted_ok"        # adjusted=true가 분할을 흡수한다 — 사용 가능
    elif not raw_shows_split and adj_continuous and abs(raw_move - 1.0) <= daily_move_tolerance:
        verdict = "no_split_visible"   # 두 시리즈 모두 연속 — 분할 정보·기간 재확인 필요
    else:
        verdict = "inconclusive"       # 백테스트 무효 사유 (BT-D2)
    return {
        "split_date": split_date,
        "split_ratio": split_ratio,
        "adjusted_move": adj_move,
        "raw_move": raw_move,
        "expected_unadjusted_jump": expected_jump,
        "verdict": verdict,
    }
