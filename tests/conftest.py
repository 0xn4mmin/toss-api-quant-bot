"""Phase 1 테스트 공용 픽스처 — 합성 CSV 데이터·소형 방법론·게이트 설정.

수치는 테스트 픽스처의 값이지 제품 코드의 값이 아니다 (§I8의 '코드에 수치 금지'는
src/ 에 적용된다). 데이터 생성은 전부 시드 고정 — 재현성 테스트의 전제.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from quantbot.backtest.config import Gates, Methodology
from quantbot.backtest.costs import CostModel
from quantbot.backtest.data import MarketDataStore
from quantbot.engine.registry import Registry


def trading_dates(start: str, n: int) -> list[str]:
    """주말 제외 순차 날짜 n개."""
    d = dt.date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def write_csv(path: Path, dates: list[str], closes: dict[str, np.ndarray]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "close", "traded_value"])
        for sym in sorted(closes):
            for d, c in zip(dates, closes[sym]):
                w.writerow([d, sym, f"{c:.6f}", "1000000"])
    return path


def gentle_uptrend(n: int, seed: int, symbols: tuple[str, ...]) -> dict[str, np.ndarray]:
    """저변동 완만 상승 — 게이트 전부 통과가 가능한 온순한 시장."""
    rng = np.random.default_rng(seed)
    out = {}
    for k, sym in enumerate(symbols):
        drift = 0.002 + 0.0002 * k
        noise = rng.normal(0.0, 0.001, size=n)
        out[sym] = 100.0 * np.cumprod(1.0 + drift + noise)
    return out


def crash_path(n: int, seed: int, crash_at: float, crash_len: int,
               crash_daily: float, symbols: tuple[str, ...]) -> dict[str, np.ndarray]:
    """상승 후 급락 — MDD 예산(INV-04)을 확실히 위반하는 경로."""
    rng = np.random.default_rng(seed)
    start = int(n * crash_at)
    out = {}
    for k, sym in enumerate(symbols):
        rets = rng.normal(0.001, 0.002, size=n)
        rets[start : start + crash_len] = crash_daily
        out[sym] = 100.0 * (1.0 + 0.01 * k) * np.cumprod(1.0 + rets)
    return out


def buy_and_hold_first(view, params) -> dict[str, float]:
    """항상 첫 종목에 전액 — 데이터 경로를 그대로 노출하는 기준 전략."""
    return {view.symbols[0]: params.get("weight", 1.0)}


def momentum_top1(view, params) -> dict[str, float]:
    """lookback 수익률 최상위 1종목 전액. 뷰가 짧으면 현금 유지."""
    lb = params["lookback"]
    best, best_r = None, -np.inf
    for s in view.symbols:
        c = view.close(s)
        if len(c) <= lb:
            continue
        r = c[-1] / c[-1 - lb] - 1.0
        if r > best_r:
            best, best_r = s, r
    return {best: 1.0} if best is not None else {}


SMALL_METH = Methodology(
    train_days=60,
    test_days=30,
    step_days=30,
    rebalance_every_n_days=5,
    trading_days_per_year=252,
    initial_capital_krw=5_000_000.0,
    bootstrap_n_samples=200,
    bootstrap_block_len=5,
    bootstrap_seed=20260706,
)

LOW_COSTS = CostModel(
    commission_rate=0.0001,
    min_commission_krw=1.0,
    slippage_rate=0.0005,
    sell_tax_rate=0.0,
    annual_gain_tax_rate=0.0,
    annual_deduction_krw=0.0,
)

LOOSE_GATES = Gates(
    g1_max_oos_mdd=0.15,
    g2_mdd_percentile=95.0,
    g2_p95_mdd_max=0.20,
    g2_stress_mdd_max=0.20,
    g2_stress_windows=(("2018-03-01", "2018-03-31"),),
    g3_min_cagr=0.0,
    g3_min_sharpe=0.5,
    g4_plateau_min_ratio=0.7,
    g5_oos_is_min_ratio=0.5,
)


@pytest.fixture
def registry(tmp_path):
    with Registry(tmp_path / "registry.db") as r:
        yield r


@pytest.fixture
def uptrend_store(tmp_path):
    n = 160
    dates = trading_dates("2018-01-02", n)
    closes = gentle_uptrend(n, seed=7, symbols=("AAA", "BBB"))
    return MarketDataStore.from_csv(
        write_csv(tmp_path / "up.csv", dates, closes)
    )


# ── 어댑터 테스트 인프라 1: 가짜 tossctl (진짜 subprocess 경로) ──────────

FAKE_TOSSCTL = Path(__file__).resolve().parent / "fake_tossctl.py"
TOSSCTL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tossctl"


def make_run_policy(**overrides):
    from quantbot.adapter.tossctl.proc import RunPolicy

    kw = dict(
        binary=str(FAKE_TOSSCTL),
        timeout_s=5.0,
        max_retries=2,
        backoff_base_s=0.0,          # 테스트에선 대기 없이
        rate_min_interval_s=0.0,
    )
    kw.update(overrides)
    return RunPolicy(**kw)


@pytest.fixture
def tossctl_runner(monkeypatch):
    from quantbot.adapter.tossctl.proc import TossctlRunner

    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(TOSSCTL_FIXTURES))
    monkeypatch.delenv("FAKE_TOSSCTL_FAIL_FILE", raising=False)
    monkeypatch.delenv("FAKE_TOSSCTL_SLEEP", raising=False)
    monkeypatch.delenv("FAKE_TOSSCTL_DUMP_ARGS", raising=False)
    return TossctlRunner(make_run_policy())


# ── 어댑터 테스트 인프라 2: 공식 API 로컬 서버 (진짜 urllib·OAuth2 경로) ──

import copy
import http.server
import json as _json
import threading
from urllib.parse import urlsplit

OFFICIAL_FIXTURES: dict[str, object] = {
    "/api/v1/prices": [
        {"symbol": "AAPL", "timestamp": "2026-07-06T05:00:00+09:00",
         "lastPrice": "213.55", "currency": "USD"},
    ],
    "/api/v1/orderbook": {
        "timestamp": "2026-07-06T05:00:00+09:00", "currency": "KRW",
        "asks": [{"price": "62000", "volume": "800"}],
        "bids": [{"price": "61900", "volume": "1200"}],
    },
    "/api/v1/trades": [
        {"price": "61950", "volume": "10", "timestamp": "2026-07-06T10:00:00+09:00",
         "currency": "KRW"},
    ],
    "/api/v1/price-limits": {
        "timestamp": "2026-07-06T09:00:00+09:00", "upperLimitPrice": "80600",
        "lowerLimitPrice": "43400", "currency": "KRW",
    },
    "/api/v1/candles": {
        "candles": [
            {"timestamp": "2026-07-01T00:00:00+09:00", "openPrice": "209.00",
             "highPrice": "211.00", "lowPrice": "208.50", "closePrice": "210.10",
             "volume": "1000000", "currency": "USD"},
            {"timestamp": "2026-07-02T00:00:00+09:00", "openPrice": "210.50",
             "highPrice": "212.30", "lowPrice": "210.00", "closePrice": "211.90",
             "volume": "1100000", "currency": "USD"},
            {"timestamp": "2026-07-03T00:00:00+09:00", "openPrice": "212.00",
             "highPrice": "214.00", "lowPrice": "211.80", "closePrice": "213.55",
             "volume": "900000", "currency": "USD"},
        ],
        "nextBefore": None,
    },
    "/api/v1/stocks": [
        {"symbol": "AAPL", "name": "애플", "englishName": "Apple Inc.",
         "isinCode": "US0378331005", "market": "NASDAQ",
         "securityType": "FOREIGN_STOCK", "isCommonShare": True, "status": "ACTIVE",
         "currency": "USD", "listDate": "1980-12-12", "delistDate": None,
         "sharesOutstanding": "15000000000", "leverageFactor": None,
         "koreanMarketDetail": None},
        {"symbol": "TQQQ", "name": "프로셰어즈 울트라프로 QQQ",
         "englishName": "ProShares UltraPro QQQ", "isinCode": "US74347X8314",
         "market": "NASDAQ", "securityType": "FOREIGN_ETF", "isCommonShare": True,
         "status": "ACTIVE", "currency": "USD", "listDate": "2010-02-09",
         "delistDate": None, "sharesOutstanding": "600000000",
         "leverageFactor": "3.0", "koreanMarketDetail": None},
    ],
    "/api/v1/stocks/005930/warnings": [
        {"warningType": "OVERHEATED", "exchange": "KRX",
         "startDate": "2026-07-01", "endDate": None},
    ],
    "/api/v1/exchange-rate": {
        "baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1352.30",
        "midRate": "1351.80", "basisPoint": "50", "rateChangeType": "UP",
        "validFrom": "2026-07-06T09:00:00+09:00", "validUntil": "2026-07-06T09:10:00+09:00",
    },
    "/api/v1/market-calendar/KR": {
        "today": {"date": "2026-07-06", "integrated": {"open": "09:00", "close": "15:30"}},
        "previousBusinessDay": {"date": "2026-07-03", "integrated": None},
        "nextBusinessDay": {"date": "2026-07-07", "integrated": None},
    },
    "/api/v1/market-calendar/US": {
        "today": {"date": "2026-07-06",
                  "dayMarket": None,
                  "preMarket": {"open": "17:00", "close": "22:30"},
                  "regularMarket": {"open": "22:30", "close": "05:00"},
                  "afterMarket": None},
        "previousBusinessDay": {"date": "2026-07-03", "dayMarket": None,
                                "preMarket": None, "regularMarket": None,
                                "afterMarket": None},
        "nextBusinessDay": {"date": "2026-07-07", "dayMarket": None, "preMarket": None,
                            "regularMarket": None, "afterMarket": None},
    },
    "/api/v1/accounts": [
        {"accountNo": "123-45-678900", "accountSeq": 1, "accountType": "BROKERAGE"},
    ],
    "/api/v1/holdings": {
        "totalPurchaseAmount": {"krw": "4800000", "usd": "1500.00"},
        "marketValue": {
            "amount": {"krw": "5100000", "usd": "1600.00"},
            "amountAfterCost": {"krw": "5095000", "usd": "1598.00"},
        },
        "profitLoss": {
            "amount": {"krw": "300000", "usd": None},
            "amountAfterCost": {"krw": "295000", "usd": None},
            "rate": "0.0625", "rateAfterCost": "0.0614",
        },
        "dailyProfitLoss": {"amount": {"krw": "12000", "usd": None}, "rate": "0.0024"},
        "items": [
            {"symbol": "AAPL", "name": "애플", "marketCountry": "US",
             "currency": "USD", "quantity": "1.5", "lastPrice": "213.55",
             "averagePurchasePrice": "205.00",
             "marketValue": {"purchaseAmount": "307.50", "amount": "320.32",
                             "amountAfterCost": "320.00"},
             "profitLoss": {"amount": "12.82", "amountAfterCost": "12.50",
                            "rate": "0.0417", "rateAfterCost": "0.0406"},
             "dailyProfitLoss": {"amount": "1.20", "rate": "0.0037"},
             "cost": {"commission": "0.32", "tax": None}},
        ],
    },
    "/api/v1/orders": {
        "orders": [
            {"orderId": "ord-1", "symbol": "AAPL", "side": "BUY",
             "orderType": "MARKET", "timeInForce": "DAY", "status": "FILLED",
             "price": None, "quantity": "1.5", "orderAmount": None,
             "currency": "USD", "orderedAt": "2026-07-01T23:35:00+09:00",
             "canceledAt": None,
             "execution": {"filledQuantity": "1.5", "averageFilledPrice": "205.00",
                           "filledAmount": "307.50", "commission": "0.32",
                           "tax": None, "filledAt": "2026-07-01T23:35:02+09:00",
                           "settlementDate": "2026-07-03"}},
        ],
        "nextCursor": None,
        "hasNext": False,
    },
    "/api/v1/buying-power": {"currency": "KRW", "cashBuyingPower": "5000000"},
    "/api/v1/sellable-quantity": {"sellableQuantity": "1.5"},
    "/api/v1/commissions": [
        {"marketCountry": "KR", "commissionRate": "0.00015",
         "startDate": None, "endDate": None},
        {"marketCountry": "US", "commissionRate": "0.001",
         "startDate": None, "endDate": None},
    ],
}
OFFICIAL_FIXTURES["/api/v1/orders/ord-1"] = copy.deepcopy(
    OFFICIAL_FIXTURES["/api/v1/orders"]["orders"][0]
)


class OfficialApiServer:
    """공식 Open API 페이크 — 진짜 HTTP로 응답해 urllib·OAuth2 경로 전체를 검증한다."""

    def __init__(self):
        self.fixtures = copy.deepcopy(OFFICIAL_FIXTURES)
        self.raw_overrides: dict[str, bytes] = {}   # path → envelope 원문 그대로
        self.fail_queue: list[tuple[int, dict, bytes]] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.token_issues = 0
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # 테스트 출력 오염 방지
                pass

            def _send(self, status, body: bytes, headers=None):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                path = urlsplit(self.path).path
                server.requests.append(("POST", path, dict(self.headers)))
                if path != "/oauth2/token":
                    self._send(404, _json.dumps(
                        {"error": {"code": "edge-blocked", "message": "no such path"}}
                    ).encode())
                    return
                length = int(self.headers.get("Content-Length", "0"))
                form = self.rfile.read(length).decode()
                if "grant_type=client_credentials" not in form:
                    self._send(400, b'{"error":{"code":"invalid-request","message":"grant"}}')
                    return
                server.token_issues += 1
                self._send(200, _json.dumps({
                    "access_token": f"tok-{server.token_issues}",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }).encode())

            def do_GET(self):
                path = urlsplit(self.path).path
                server.requests.append(("GET", path, dict(self.headers)))
                if server.fail_queue:
                    status, headers, body = server.fail_queue.pop(0)
                    self._send(status, body, headers)
                    return
                auth = self.headers.get("Authorization", "")
                if auth != f"Bearer tok-{server.token_issues}" or server.token_issues == 0:
                    self._send(401, _json.dumps(
                        {"error": {"code": "invalid-token", "message": "bad token"}}
                    ).encode())
                    return
                if path in server.raw_overrides:
                    self._send(200, server.raw_overrides[path])
                    return
                if path in server.fixtures:
                    self._send(200, _json.dumps(
                        {"result": server.fixtures[path]}, ensure_ascii=False
                    ).encode())
                    return
                self._send(404, _json.dumps(
                    {"error": {"code": "edge-blocked", "message": f"no fixture {path}"}}
                ).encode())

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def official_server():
    server = OfficialApiServer()
    yield server
    server.stop()


def make_openapi_policy(base_url: str, **overrides):
    from quantbot.adapter.official.http import OpenApiPolicy

    groups = ("AUTH", "ACCOUNT", "ASSET", "STOCK", "MARKET_INFO", "MARKET_DATA",
              "MARKET_DATA_CHART", "ORDER_HISTORY", "ORDER_INFO")
    kw = dict(
        base_url=base_url,
        timeout_s=5.0,
        max_retries=2,
        backoff_base_s=0.0,
        group_tps={g: 10000.0 for g in groups},  # 테스트에선 사실상 무제한
    )
    kw.update(overrides)
    return OpenApiPolicy(**kw)


@pytest.fixture
def official_client(official_server):
    from quantbot.adapter.official.http import Credentials, OpenApiClient

    return OpenApiClient(
        make_openapi_policy(official_server.base_url),
        Credentials(client_id="cid-test", client_secret="sec-test"),
        account_seq="1",
    )
