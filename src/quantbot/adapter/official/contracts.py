"""공식 Open API 계약 (IMPL-03 v1.1) — OAS 3.1 명세 v1.1.5의 미러.

추측이 아니라 문서가 근거다: 필드명·필수 여부·nullable·enum을 명세 그대로 옮겼다.
수치는 전부 문자열 타입(명세 그대로 — 정밀도 보존), envelope은 {"result": ...}이며
envelope에 미지 키가 있으면 그것도 SchemaDrift다 (fail-closed).
"""

from __future__ import annotations

from typing import Literal

from quantbot.adapter.contracts import Contract, SchemaDrift, SchemaDriftError, validate
from quantbot.adapter.official.http import OpenApiClient

SOURCE = "official"

Currency = Literal["KRW", "USD"]
MarketCountry = Literal["KR", "US"]


class _Envelope(Contract):
    """공통 ApiResponse envelope — result 외 키는 존재할 수 없다."""

    result: object


def call_api(
    client: OpenApiClient,
    path: str,
    group: str,
    model: type[Contract],
    *,
    params: dict[str, str] | None = None,
    with_account: bool = False,
    many: bool = False,
):
    """GET → envelope 검증 → result 계약 검증 — 공식 표면의 유일한 통과 경로."""
    body = client.get(path, group, params, with_account=with_account)
    command = ("GET", path)
    env = validate(SOURCE, command, body, _Envelope)
    result = env.result
    if many:
        if not isinstance(result, list):
            raise SchemaDriftError(SchemaDrift(
                source=SOURCE, command=command, model=model.__name__,
                detail=f"result가 배열이 아니다: {type(result).__name__}",
            ))
        return [validate(SOURCE, command, item, model) for item in result]
    return validate(SOURCE, command, result, model)


# ═══ Market Data — 시세 ═════════════════════════════════════════════════


class PriceResponse(Contract):
    symbol: str
    timestamp: str | None = None
    lastPrice: str
    currency: Currency


class OrderbookEntry(Contract):
    price: str
    volume: str


class OrderbookResponse(Contract):
    timestamp: str | None = None
    currency: Currency
    asks: list[OrderbookEntry]
    bids: list[OrderbookEntry]


class Trade(Contract):
    price: str
    volume: str
    timestamp: str
    currency: Currency


class PriceLimitResponse(Contract):
    timestamp: str
    upperLimitPrice: str | None = None
    lowerLimitPrice: str | None = None
    currency: Currency


class Candle(Contract):
    timestamp: str
    openPrice: str
    highPrice: str
    lowPrice: str
    closePrice: str
    volume: str
    currency: Currency


class CandlePageResponse(Contract):
    candles: list[Candle]
    nextBefore: str | None = None


# ═══ Stock Info — 종목 마스터·유의사항 ═════════════════════════════════

SECURITY_TYPES_ETP = ("ETF", "FOREIGN_ETF", "ETN")  # INV-11 판정 대상 유형


class KrMarketDetail(Contract):
    """국내 시장 상세 — 명세 필드 확정 전 초안(국내 종목에만 제공)."""

    model_config = Contract.model_config | {"extra": "allow"}  # 하위 필드는 실측 후 고정


class StockInfo(Contract):
    symbol: str
    name: str
    englishName: str
    isinCode: str
    market: Literal["KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "AMEX", "KR_ETC", "US_ETC"]
    securityType: Literal[
        "STOCK", "FOREIGN_STOCK", "DEPOSITARY_RECEIPT", "INFRASTRUCTURE_FUND",
        "REIT", "ETF", "FOREIGN_ETF", "ETN", "STOCK_WARRANTS",
    ]
    isCommonShare: bool
    status: Literal["SCHEDULED", "ACTIVE", "DELISTED"]  # BT-D1 생존편향 재구성 지원
    currency: Currency
    listDate: str | None = None
    delistDate: str | None = None
    sharesOutstanding: str
    leverageFactor: str | None = None  # INV-11 기계 검증 입력 — ETF/ETN 외에는 null이 정상
    koreanMarketDetail: KrMarketDetail | None = None


class StockWarning(Contract):
    warningType: str  # 명세: "unknown code를 허용하도록 구현" — enum 고정 대신 str
    exchange: str | None = None
    startDate: str | None = None
    endDate: str | None = None


# ═══ Market Info — 환율·장 운영 시간 ═══════════════════════════════════


class ExchangeRateResponse(Contract):
    baseCurrency: Currency
    quoteCurrency: Currency
    rate: str
    midRate: str
    basisPoint: str
    rateChangeType: Literal["UP", "EQUAL", "DOWN"]
    validFrom: str
    validUntil: str


class _Session(Contract):
    """장 세션 시간 — 하위 필드는 실측 후 고정 (문서 렌더 미확인 구간)."""

    model_config = Contract.model_config | {"extra": "allow"}


class KrMarketDay(Contract):
    date: str
    integrated: _Session | None = None


class KrMarketCalendarResponse(Contract):
    today: KrMarketDay
    previousBusinessDay: KrMarketDay
    nextBusinessDay: KrMarketDay


class UsMarketDay(Contract):
    date: str
    dayMarket: _Session | None = None
    preMarket: _Session | None = None
    regularMarket: _Session | None = None
    afterMarket: _Session | None = None


class UsMarketCalendarResponse(Contract):
    today: UsMarketDay
    previousBusinessDay: UsMarketDay
    nextBusinessDay: UsMarketDay


# ═══ Account · Asset ════════════════════════════════════════════════════


class Account(Contract):
    accountNo: str
    accountSeq: int
    accountType: Literal[
        "BROKERAGE", "OVERSEAS_DERIVATIVES", "PENSION_SAVINGS", "RESHORING_INVESTMENT"
    ]


class DualAmount(Contract):
    krw: str
    usd: str | None = None


class OverviewMarketValue(Contract):
    amount: DualAmount
    amountAfterCost: DualAmount


class OverviewProfitLoss(Contract):
    amount: DualAmount
    amountAfterCost: DualAmount
    rate: str
    rateAfterCost: str


class OverviewDailyProfitLoss(Contract):
    amount: DualAmount
    rate: str


class MarketValue(Contract):
    purchaseAmount: str
    amount: str
    amountAfterCost: str


class ProfitLoss(Contract):
    amount: str
    amountAfterCost: str
    rate: str
    rateAfterCost: str


class DailyProfitLoss(Contract):
    amount: str
    rate: str


class Cost(Contract):
    commission: str
    tax: str | None = None


class HoldingsItem(Contract):
    symbol: str
    name: str
    marketCountry: MarketCountry
    currency: Currency
    quantity: str
    lastPrice: str
    averagePurchasePrice: str
    marketValue: MarketValue
    profitLoss: ProfitLoss
    dailyProfitLoss: DailyProfitLoss
    cost: Cost


class HoldingsOverview(Contract):
    totalPurchaseAmount: DualAmount
    marketValue: OverviewMarketValue
    profitLoss: OverviewProfitLoss
    dailyProfitLoss: OverviewDailyProfitLoss
    items: list[HoldingsItem]


# ═══ Order History · Order Info — 조회 전용 (주문 발행은 Phase 4 표면) ═══


class OrderExecution(Contract):
    filledQuantity: str
    averageFilledPrice: str | None = None
    filledAmount: str | None = None
    commission: str | None = None
    tax: str | None = None
    filledAt: str | None = None
    settlementDate: str | None = None


class OrderRecord(Contract):
    orderId: str
    symbol: str
    side: Literal["BUY", "SELL"]
    orderType: Literal["LIMIT", "MARKET"]
    timeInForce: Literal["DAY", "CLS", "OPG"]
    status: Literal[
        "PENDING", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIAL_FILLED", "FILLED",
        "CANCELED", "REJECTED", "CANCEL_REJECTED", "REPLACE_REJECTED", "REPLACED",
    ]
    price: str | None = None
    quantity: str
    orderAmount: str | None = None
    currency: Currency
    orderedAt: str
    canceledAt: str | None = None
    execution: OrderExecution


class PaginatedOrderResponse(Contract):
    orders: list[OrderRecord]
    nextCursor: str | None = None
    hasNext: bool


class BuyingPowerResponse(Contract):
    currency: Currency
    cashBuyingPower: str


class SellableQuantityResponse(Contract):
    sellableQuantity: str


class Commission(Contract):
    marketCountry: MarketCountry
    commissionRate: str
    startDate: str | None = None
    endDate: str | None = None
