"""백테스트 구성요소 단위 검사 — 폴드·그리드·비용·통계·설정 로더."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from quantbot.backtest import walkforward
from quantbot.backtest.config import (
    BacktestConfigError,
    load_gates,
    load_grid,
    load_methodology,
)
from quantbot.backtest.costs import CostModel
from quantbot.backtest.sim import equity_from_returns, mdd, sharpe
from conftest import LOW_COSTS, SMALL_METH


def test_folds_are_rolling_not_anchored():
    folds = walkforward.make_folds(160, SMALL_METH)
    assert [(f.train_start, f.train_end, f.test_start, f.test_end) for f in folds] == [
        (0, 60, 60, 90),
        (30, 90, 90, 120),
        (60, 120, 120, 150),
    ]
    with pytest.raises(walkforward.WalkForwardError):
        walkforward.make_folds(89, SMALL_METH)  # train+test 미달


def test_grid_configs_deterministic_product():
    grid = {"b": [1, 2], "a": [True, False]}
    configs = walkforward.grid_configs(grid)
    assert len(configs) == 4
    assert configs[0] == {"a": True, "b": 1}  # 정렬 키 순서


def test_grid_neighbors_one_axis_one_step():
    grid = {"x": [1, 2, 3], "y": [10, 20]}
    nbs = walkforward.grid_neighbors(grid, {"x": 2, "y": 10})
    assert {(n["x"], n["y"]) for n in nbs} == {(1, 10), (3, 10), (2, 20)}


def test_plateau_median_selection():
    grid = {"lb": [3, 5, 8], "flag": [True, False]}
    best = [
        {"lb": 3, "flag": True},
        {"lb": 5, "flag": True},
        {"lb": 8, "flag": False},
    ]
    sel = walkforward.select_plateau_median(grid, best)
    assert sel == {"lb": 5, "flag": True}


def test_cost_model_min_commission_floor_and_asymmetry():
    cm = dataclasses.replace(
        LOW_COSTS, commission_rate=0.001, min_commission_krw=500.0,
        sell_tax_rate=0.002, annual_gain_tax_rate=0.22,
        annual_deduction_krw=1000.0,
    )
    assert cm.commission(10_000.0) == 500.0        # 하한 발동 (BT-G6 리스크)
    assert cm.commission(1_000_000.0) == 1000.0    # 정률 구간
    assert cm.sell_tax(100_000.0) == 200.0
    assert cm.annual_tax(500.0) == 0.0             # 공제 이하
    assert cm.annual_tax(2000.0) == pytest.approx(220.0)
    assert cm.buy_price(100.0) > 100.0 > cm.sell_price(100.0)  # 항상 불리한 방향


def test_mdd_and_sharpe_basics():
    eq = np.array([100.0, 110.0, 88.0, 120.0])
    assert mdd(eq) == pytest.approx(0.2)
    flat = np.zeros(10)
    assert sharpe(flat, 252) == 0.0
    eq2 = equity_from_returns(np.array([0.1, -0.5]), 100.0)
    assert eq2[-1] == pytest.approx(55.0)


def test_repo_config_files_load():
    """저장소에 실제로 존재하는 설정 파일들이 파싱·검증을 통과한다."""
    meth, costs = load_methodology("config/backtest.yaml")
    assert meth.train_days == 756 and meth.test_days == 252  # BT-03: 3년/1년
    cm = CostModel.from_config(costs)
    assert cm.min_commission_krw > 0
    gates = load_gates("config/backtest_gates.yaml")
    assert gates.g1_max_oos_mdd == pytest.approx(0.15)  # INV-04 직결
    assert any("2020-02" in w[0] for w in gates.g2_stress_windows)  # 스트레스 창 의무
    grid = load_grid("config/grids/momentum-core.yaml")
    assert set(grid) >= {"lookback_wk", "skip_wk", "abs_filter", "ma_len"}


def test_config_loader_rejects_missing_sections(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(BacktestConfigError):
        load_methodology(p)
    with pytest.raises(BacktestConfigError):
        load_gates(p)
