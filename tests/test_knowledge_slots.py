"""v1.4 확장 슬롯 — 변동성 타게팅·듀얼 모멘텀의 수식 대조와 파이프라인 통합."""

from __future__ import annotations

import numpy as np
import pytest

from quantbot.strategy.loader import load_strategy
from quantbot.strategy.schema import StrategySchemaError, parse_strategy
from quantbot.strategy.slots.dual_momentum import dual_momentum_select
from quantbot.strategy.slots.pipeline import (
    build_dual_momentum_signal,
    build_us_core_signal,
)
from quantbot.strategy.slots.vol_target import vol_target_scalar
from test_strategy import _valid_dict


# ── vol targeting (Moreira-Muir 계열) — 수식 대조 ───────────────────────


def test_vol_target_scalar_matches_formula():
    # 일일 ±1% 교대 수익률 → 실현 연변동성이 목표(10%)보다 크게 → 스칼라 < 1
    closes = 100.0 * np.cumprod(1 + np.tile([0.01, -0.01], 40))
    s = vol_target_scalar(closes, annual_target=0.10, lookback_days=60,
                          trading_days_per_year=252)
    rets = closes[-61:][1:] / closes[-61:][:-1] - 1
    expected = 0.10 / (np.std(rets, ddof=1) * np.sqrt(252))
    assert s == pytest.approx(expected)
    assert 0.0 < s < 1.0


def test_vol_target_caps_at_one_and_fails_safe():
    calm = 100.0 * np.cumprod(1 + np.full(80, 0.0001))   # 초저변동 → 상한 1.0
    assert vol_target_scalar(calm, 0.10, 60, 252) == 1.0  # 레버리지 금지 (INV-02)
    short = np.array([100.0, 101.0])                      # 표본 부족 → 스케일 안 함
    assert vol_target_scalar(short, 0.10, 60, 252) == 1.0
    with pytest.raises(ValueError):
        vol_target_scalar(calm, 1.5, 60, 252)
    with pytest.raises(ValueError):
        vol_target_scalar(calm, 0.1, 1, 252)


# ── dual momentum (Antonacci 계열) — 선택 규칙 대조 ─────────────────────


def _closes(**growth):
    return {s: 100.0 * np.cumprod(1 + np.full(60, g)) for s, g in growth.items()}


def test_dual_momentum_relative_and_absolute():
    closes = _closes(SPY=0.002, TLT=0.001, GLD=-0.001)
    assert dual_momentum_select(closes, lookback=30, top_n=2) == ["SPY", "TLT"]
    # 절대 필터: 상위여도 음수면 제외 → 현금
    bear = _closes(SPY=-0.001, TLT=-0.002, GLD=-0.003)
    assert dual_momentum_select(bear, lookback=30, top_n=2) == []


def test_dual_momentum_signal_weights_and_cash():
    closes = _closes(SPY=0.002, TLT=0.001, GLD=-0.001)

    class View:
        symbols = tuple(sorted(closes))
        def close(self, s):
            return closes[s]

    fn = build_dual_momentum_signal({"lookback": 30, "top_n": 2}, cap=0.99)
    w = fn(View(), None)
    assert w == {"SPY": pytest.approx(0.5), "TLT": pytest.approx(0.5)}  # 1/top_n
    bear = _closes(SPY=-0.001, TLT=-0.002, GLD=-0.003)

    class BearView(View):
        def close(self, s):
            return bear[s]

    assert fn(BearView(), None) == {}                     # 전액 현금
    # INV-01 캡과의 상호작용: 캡 0.12면 자산당 12%로 클리핑, 잔여 현금
    capped = build_dual_momentum_signal({"lookback": 30, "top_n": 1}, cap=0.12)
    w2 = capped(View(), None)
    assert w2 == {"SPY": pytest.approx(0.12)}


def test_vol_target_overlay_scales_us_core_weights():
    # 고변동 상승 시장 — vol target이 노출을 줄인다
    rng = np.random.default_rng(3)
    closes = {s: 100.0 * np.cumprod(1 + 0.002 + rng.normal(0, 0.03, 120))
              for s in ("AAA", "BBB")}

    class View:
        symbols = ("AAA", "BBB")
        def close(self, s):
            return closes[s]

    base_params = {"lookback": 20, "skip": 1, "abs_filter": False,
                   "n": 2, "exit_buffer": 1.5}
    plain = build_us_core_signal(base_params, cap=0.99)(View(), None)
    scaled = build_us_core_signal(
        base_params, cap=0.99, vol_target_spec=(0.10, 60, 252)
    )(View(), None)
    assert sum(scaled.values()) < sum(plain.values()) * 0.7  # 연 3%대 변동성 목표 대비 축소


# ── 스키마·파일 ─────────────────────────────────────────────────────────


def test_sizing_vol_target_schema_validation():
    d = _valid_dict()
    d["sizing"].update(vol_target_annual=0.10, vol_lookback_days=60)
    assert parse_strategy(d).sizing.vol_target_annual == 0.10
    d2 = _valid_dict()
    d2["sizing"].update(vol_target_annual=0.10)          # lookback 없이 — 거부
    with pytest.raises(StrategySchemaError, match="함께"):
        parse_strategy(d2)
    d3 = _valid_dict()
    d3["sizing"].update(vol_target_annual=1.5, vol_lookback_days=60)
    with pytest.raises(StrategySchemaError, match="vol_target_annual"):
        parse_strategy(d3)


def test_dual_momentum_example_file_and_grid():
    from quantbot.backtest.config import load_grid

    s = load_strategy("strategies/dual-momentum.v1.yaml")
    assert s.meta.id == "dual-momentum"
    assert {d.slot for d in s.signals} == {"dual_momentum"}
    assert s.sizing.vol_target_annual == 0.10
    grid = load_grid("config/grids/dual-momentum.yaml")
    assert set(grid) == {"lookback_wk", "top_n", "skip_wk"}


# ── e2e: 러너가 듀얼 모멘텀으로 게이트 판정까지 완주 ────────────────────


def test_dual_momentum_end_to_end_gate(registry, tmp_path):
    from quantbot.backtest import judge, prereg, walkforward
    from quantbot.backtest.data import MarketDataStore
    from conftest import LOOSE_GATES, LOW_COSTS, SMALL_METH, trading_dates, write_csv

    n = 160
    dates = trading_dates("2018-01-02", n)
    rng = np.random.default_rng(9)
    closes = {
        "SPY": 100.0 * np.cumprod(1 + 0.0020 + rng.normal(0, 0.001, n)),
        "TLT": 100.0 * np.cumprod(1 + 0.0005 + rng.normal(0, 0.001, n)),
        "GLD": 100.0 * np.cumprod(1 + 0.0010 + rng.normal(0, 0.001, n)),
    }
    store = MarketDataStore.from_csv(write_csv(tmp_path / "a.csv", dates, closes))

    def signal_fn(params):
        fn = build_dual_momentum_signal(
            {"lookback": params["lookback"], "top_n": 2}, cap=0.99,
            vol_target_spec=(0.10, 20, 252),
        )
        return lambda view: fn(view, None)

    grid = {"lookback": [10, 20]}
    dr = (store.date(0), store.date(n - 1))
    prereg.seal(registry, "dm-e2e", grid, dr, walkforward.folds_spec(SMALL_METH))
    res = judge.evaluate_oos(registry, store, "dm-e2e", grid, dr,
                             signal_fn, LOW_COSTS, SMALL_METH, LOOSE_GATES)
    assert res.transition in ("paper", "rejected")        # 판정까지 완주
    assert res.n_configs_tried == 2 * len(res.metrics["per_fold_best"])

def test_momentum_core_v2_file_parses():
    s = load_strategy("strategies/momentum-core.v2.yaml")
    assert (s.meta.id, s.meta.version) == ("momentum-core", 2)
    assert s.sizing.vol_target_annual == 0.12 and s.sizing.vol_lookback_days == 60
    assert s.universe["us_core"].max_symbols == 40


def test_vol_scalar_band_suppresses_churn():
    """스칼라 변경 밴드 — 작은 변동은 직전 노출 유지 (v3 회전율 5.0x 교훈)."""
    from quantbot.strategy.slots.pipeline import _ScalarSmoother

    s = _ScalarSmoother(band=0.10)
    assert s.smooth(0.80) == 0.80          # 최초 — 그대로
    assert s.smooth(0.85) == 0.80          # 밴드 안 — 유지
    assert s.smooth(0.74) == 0.80          # 밴드 안(0.06) — 유지
    assert s.smooth(0.60) == 0.60          # 밴드 밖 — 갱신
    assert s.smooth(0.65) == 0.60          # 새 기준점 대비 밴드 안
    off = _ScalarSmoother(band=None)
    assert off.smooth(0.5) == 0.5 and off.smooth(0.51) == 0.51  # 밴드 없음 = 통과


def test_vol_scalar_band_schema():
    d = _valid_dict()
    d["sizing"].update(vol_scalar_band=0.1)   # vol_target 없이 — 거부
    with pytest.raises(StrategySchemaError, match="vol_scalar_band"):
        parse_strategy(d)
    d2 = _valid_dict()
    d2["sizing"].update(vol_target_annual=0.12, vol_lookback_days=60,
                        vol_scalar_band=0.1)
    assert parse_strategy(d2).sizing.vol_scalar_band == 0.1


# ── dual momentum v2 — 히스테리시스·월간 선택·시뮬 밴드 (2026-07-07) ────


def test_dual_momentum_hysteresis_keeps_holdings_in_buffer():
    from quantbot.strategy.slots.dual_momentum import dual_momentum_select

    # 순위: A > B > C > D (전부 양수)
    closes = _closes(A=0.004, B=0.003, C=0.002, D=0.001)
    # 보유 C: 순위 3 ≤ top_n(2)×1.5=3 → 유지, 빈 슬롯은 최상위 A
    sel = dual_momentum_select(closes, lookback=30, top_n=2,
                               holdings=["C"], exit_buffer=1.5)
    assert sel == ["A", "C"]
    # 보유 D: 순위 4 > 3 → 이탈 → 신규 top 2
    sel2 = dual_momentum_select(closes, lookback=30, top_n=2,
                                holdings=["D"], exit_buffer=1.5)
    assert sel2 == ["A", "B"]
    # 보유 자산이 절대 모멘텀 음전 → 순위 무관 즉시 이탈
    bear_c = _closes(A=0.004, B=0.003, C=-0.001, D=0.001)
    sel3 = dual_momentum_select(bear_c, lookback=30, top_n=2,
                                holdings=["C"], exit_buffer=1.5)
    assert "C" not in sel3


def test_dual_momentum_monthly_selection_cycle():
    """선택은 매 4번째 호출에서만 — 사이에는 보유 유지, 음전만 즉시 이탈."""
    import numpy as np
    from quantbot.strategy.slots.pipeline import build_dual_momentum_signal

    up = {s: 100.0 * np.cumprod(1 + g * np.ones(60))
          for s, g in {"SPY": 0.003, "TLT": 0.002, "GLD": 0.001}.items()}
    flipped = {s: 100.0 * np.cumprod(1 + g * np.ones(60))
               for s, g in {"SPY": 0.001, "TLT": 0.002, "GLD": 0.003}.items()}

    def view(data):
        class V:
            symbols = tuple(sorted(data))
            def close(self, s):
                return data[s]
        return V()

    fn = build_dual_momentum_signal(
        {"lookback": 30, "top_n": 2, "exit_buffer": 1.0}, cap=0.50,
        selection_every=4,
    )
    assert set(fn(view(up), None)) == {"SPY", "TLT"}       # 1회차: 선택
    for _ in range(3):                                     # 2~4회차: 순위 뒤집혀도 유지
        assert set(fn(view(flipped), None)) == {"SPY", "TLT"}
    assert set(fn(view(flipped), None)) == {"GLD", "TLT"}  # 5회차: 재선택


def test_sim_no_trade_band_suppresses_drift_trades(uptrend_store):
    """§S5 3단 충실도: 고정 목표 비중이면 최초 매수 후 드리프트 매매가 없다."""
    from quantbot.backtest.sim import simulate
    from conftest import LOW_COSTS, SMALL_METH, buy_and_hold_first

    no_band = simulate(uptrend_store, 0, 40, buy_and_hold_first, {"weight": 0.5},
                       LOW_COSTS, SMALL_METH)
    banded = simulate(uptrend_store, 0, 40, buy_and_hold_first, {"weight": 0.5},
                      LOW_COSTS, SMALL_METH, no_trade_band=0.02)
    assert len(no_band.order_notionals) > 1        # 매주 드리프트 매매 (기존 동작)
    assert len(banded.order_notionals) == 1        # 밴드 안 — 최초 매수뿐


def test_monthly_cadence_schema_and_inv05():
    from quantbot.engine.approval import static_invariant_check
    from quantbot.engine.invariants import load_invariants

    d = _valid_dict()
    d["cadence"]["rebalance"] = "monthly"
    s = parse_strategy(d)
    violations = static_invariant_check(
        s, load_invariants(), {"us_core": ["AAA"]},
        whitelist={"us_core": {"AAA"}}, stock_master={"AAA": ("STOCK", None)},
    )
    assert not any("INV-05" in v for v in violations)      # monthly는 합법


def test_dual_momentum_v2_file_parses():
    s = load_strategy("strategies/dual-momentum.v2.yaml")
    assert s.cadence.rebalance == "monthly"
    assert s.entry_exit.exit.params["exit_buffer"] == 1.5
    assert s.sizing.no_trade_band == 0.02
