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


# ── Phase 2: 어댑터 테스트 인프라 (가짜 tossctl — 진짜 subprocess 경로) ──

FAKE_TOSSCTL = Path(__file__).resolve().parent / "fake_tossctl.py"
TOSSCTL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tossctl"


def make_run_policy(**overrides):
    from quantbot.adapter.proc import RunPolicy

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
    from quantbot.adapter.proc import TossctlRunner

    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(TOSSCTL_FIXTURES))
    monkeypatch.delenv("FAKE_TOSSCTL_FAIL_FILE", raising=False)
    monkeypatch.delenv("FAKE_TOSSCTL_SLEEP", raising=False)
    monkeypatch.delenv("FAKE_TOSSCTL_DUMP_ARGS", raising=False)
    return TossctlRunner(make_run_policy())
