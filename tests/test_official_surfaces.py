"""공식 API 조회 표면 전수 검사 + 계약 위반 주입 → SchemaDrift 전체 거부.

계약은 OAS v1.1.5 미러 — 수치는 문자열, envelope은 {"result": ...}.
"""

from __future__ import annotations

import json

import pytest

from quantbot.adapter.contracts import SchemaDriftError
from quantbot.adapter.official import acct, ledger, md, mkt, tradeinfo
from quantbot.adapter.official.contracts import Candle


def test_market_data_surfaces(official_server, official_client):
    p = md.prices(official_client, ["AAPL"])
    assert p[0].symbol == "AAPL" and p[0].lastPrice == "213.55"  # 문자열 그대로
    ob = md.orderbook(official_client, "005930")
    assert float(ob.bids[0].price) < float(ob.asks[0].price)
    assert md.trades(official_client, "005930")[0].currency == "KRW"
    lim = md.price_limits(official_client, "005930")
    assert lim.upperLimitPrice == "80600"
    page = md.candles(official_client, "AAPL", "1d", 3)
    assert [c.closePrice for c in page.candles] == ["210.10", "211.90", "213.55"]
    # 백테스트 적재 기본값: adjusted=true 가 쿼리에 실린다 (BT-D2)
    get_paths = [r for r in official_server.requests if r[0] == "GET"]
    assert any("/api/v1/candles" in p for _, p, _ in get_paths)


def test_stock_info_carries_inv11_fields(official_client):
    infos = md.stocks(official_client, ["AAPL", "TQQQ"])
    by_symbol = {s.symbol: s for s in infos}
    assert by_symbol["AAPL"].leverageFactor is None          # 비ETF — null이 정상
    assert by_symbol["AAPL"].securityType == "FOREIGN_STOCK"
    assert by_symbol["TQQQ"].leverageFactor == "3.0"         # INV-11이 걸러낼 대상
    assert by_symbol["TQQQ"].securityType == "FOREIGN_ETF"
    assert by_symbol["AAPL"].status == "ACTIVE"              # BT-D1 지원 필드


def test_remaining_surfaces(official_client):
    assert md.warnings(official_client, "005930")[0].warningType == "OVERHEATED"
    fx = mkt.exchange_rate(official_client, "USD", "KRW")
    assert (fx.baseCurrency, fx.rate) == ("USD", "1352.30")
    kr = mkt.market_calendar_kr(official_client)
    assert kr.today.date == "2026-07-06" and kr.previousBusinessDay.integrated is None
    us = mkt.market_calendar_us(official_client)
    assert us.today.regularMarket is not None
    accts = acct.accounts(official_client)
    assert accts[0].accountSeq == 1
    hold = acct.holdings(official_client)
    assert hold.items[0].quantity == "1.5" and hold.totalPurchaseAmount.krw == "4800000"
    orders = ledger.orders_list(official_client)
    assert orders.orders[0].status == "FILLED" and orders.hasNext is False
    detail = ledger.order_detail(official_client, "ord-1")
    assert detail.execution.filledQuantity == "1.5"
    bp = tradeinfo.buying_power(official_client, "KRW")
    assert bp.cashBuyingPower == "5000000"
    assert tradeinfo.sellable_quantity(official_client, "AAPL").sellableQuantity == "1.5"
    comms = tradeinfo.commissions(official_client)
    assert {c.marketCountry for c in comms} == {"KR", "US"}


# ── 계약 위반 주입 → 전체 거부 (fail-closed) ────────────────────────────


def test_numeric_field_as_json_number_is_drift(official_server, official_client):
    """명세는 수치를 문자열로 정의한다 — JSON 숫자로 오면 그것도 드리프트다 (strict)."""
    official_server.fixtures["/api/v1/prices"][0]["lastPrice"] = 213.55  # str → number
    with pytest.raises(SchemaDriftError) as exc:
        md.prices(official_client, ["AAPL"])
    assert exc.value.drift.source == "official"
    assert exc.value.drift.model == "PriceResponse"


@pytest.mark.parametrize("mutation", ["extra_field", "missing_field", "bad_enum"])
def test_result_mutations_are_rejected_whole(mutation, official_server, official_client):
    fx = official_server.fixtures["/api/v1/exchange-rate"]
    if mutation == "extra_field":
        fx["spread"] = "0.5"
    elif mutation == "missing_field":
        del fx["midRate"]
    else:
        fx["rateChangeType"] = "SIDEWAYS"
    with pytest.raises(SchemaDriftError):
        mkt.exchange_rate(official_client, "USD", "KRW")


def test_envelope_violations_are_drift(official_server, official_client):
    """envelope에 미지 키가 있거나 result가 없으면 그것도 SchemaDrift."""
    official_server.raw_overrides["/api/v1/exchange-rate"] = json.dumps(
        {"result": official_server.fixtures["/api/v1/exchange-rate"], "extra": 1}
    ).encode()
    with pytest.raises(SchemaDriftError):
        mkt.exchange_rate(official_client, "USD", "KRW")
    official_server.raw_overrides["/api/v1/exchange-rate"] = b'{"data": {}}'
    with pytest.raises(SchemaDriftError):
        mkt.exchange_rate(official_client, "USD", "KRW")


def test_array_result_where_object_expected_is_drift(official_server, official_client):
    official_server.raw_overrides["/api/v1/commissions"] = json.dumps(
        {"result": {"not": "a list"}}
    ).encode()
    with pytest.raises(SchemaDriftError, match="배열이 아니다"):
        tradeinfo.commissions(official_client)


def test_contracts_are_frozen(official_client):
    q = md.prices(official_client, ["AAPL"])[0]
    with pytest.raises(Exception):
        q.lastPrice = "0"


# ── BT-D2 — adjusted true/false 실측 비교 (STRAT §S7 v1.2) ─────────────


def _candles(closes: list[str]) -> list[Candle]:
    return [
        Candle(
            timestamp=f"2026-06-{i + 1:02d}T00:00:00+09:00",
            openPrice=c, highPrice=c, lowPrice=c, closePrice=c,
            volume="1000", currency="USD",
        )
        for i, c in enumerate(closes)
    ]


TOL = 0.05


def test_adjusted_ok_verdict():
    """adjusted 시리즈는 연속, raw 시리즈는 1/4 점프 → 수정주가 신뢰 가능."""
    adjusted = _candles(["25.00", "25.25", "25.10", "25.40"])
    raw = _candles(["100.00", "101.00", "25.20", "25.40"])  # 4:1 분할이 그대로 보임
    report = md.adjusted_price_report(adjusted, raw, "2026-06-03", 4.0, TOL)
    assert report["verdict"] == "adjusted_ok"
    assert report["expected_unadjusted_jump"] == pytest.approx(0.25)


def test_no_split_visible_verdict():
    """두 시리즈 모두 연속 — 분할 정보·조회 기간 재확인 필요."""
    series = _candles(["100.00", "101.00", "100.50", "102.00"])
    report = md.adjusted_price_report(series, series, "2026-06-03", 4.0, TOL)
    assert report["verdict"] == "no_split_visible"


def test_inconclusive_verdict_blocks_backtest():
    """설명 불가능한 움직임 → inconclusive — 미해결이면 백테스트 무효 (BT-D2)."""
    adjusted = _candles(["100.00", "101.00", "60.00", "61.00"])
    raw = _candles(["100.00", "101.00", "60.00", "61.00"])
    report = md.adjusted_price_report(adjusted, raw, "2026-06-03", 4.0, TOL)
    assert report["verdict"] == "inconclusive"


def test_report_requires_split_context():
    series = _candles(["100.00", "101.00"])
    with pytest.raises(ValueError):
        md.adjusted_price_report(series, series, "2099-01-01", 4.0, TOL)
    with pytest.raises(ValueError):
        md.adjusted_price_report(series, series, "2026-06-01", 4.0, TOL)
    with pytest.raises(ValueError):
        md.adjusted_price_report(series, series, "2026-06-02", 1.0, TOL)
