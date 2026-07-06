"""DoD: 센티널 look-ahead 테스트 (BT-D3) — 미래에 극단값을 심어도 결과 불변.

as_of 뷰는 미래를 '감춘' 게 아니라 '없는' 것이므로, 시점 T까지의 판정 결과는
T 이후 데이터가 무엇이든 바이트 단위로 동일해야 한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantbot.backtest import judge
from quantbot.backtest.data import MarketDataStore
from quantbot.engine.registry import Registry
from conftest import (
    LOOSE_GATES,
    LOW_COSTS,
    SMALL_METH,
    gentle_uptrend,
    momentum_top1,
    trading_dates,
    write_csv,
)

N = 160
T_IDX = 100  # 이 시점까지만 판정에 사용 — 이후는 센티널 구역
GRID = {"lookback": [3, 5, 8]}


def _make_store(tmp_path, name: str, poison: bool) -> MarketDataStore:
    dates = trading_dates("2018-01-02", N)
    closes = gentle_uptrend(N, seed=11, symbols=("AAA", "BBB"))
    if poison:
        for sym in closes:
            closes[sym] = closes[sym].copy()
            # 미래 구간에 극단값: 1000배 폭등 후 99% 폭락 — 시그널이 미동도 않아야 함
            closes[sym][T_IDX + 1 :] *= 1000.0
            closes[sym][-3:] *= 0.01
    return MarketDataStore.from_csv(write_csv(tmp_path / name, dates, closes))


def test_as_of_view_has_no_future(uptrend_store):
    """구조 검사: as_of(i) 뷰에 i 이후 인덱스가 존재하지 않고, 배열은 읽기 전용."""
    i = 50
    view = uptrend_store.as_of(i)
    assert len(view) == i + 1
    for s in view.symbols:
        assert len(view.close(s)) == i + 1
        with pytest.raises((ValueError, RuntimeError)):
            view.close(s)[0] = 999.0


def test_sentinel_future_extremes_do_not_move_judgement(tmp_path):
    """T 이후에 극단값을 심은 스토어와 원본 스토어의 판정 아티팩트가 바이트 동일."""
    clean = _make_store(tmp_path, "clean.csv", poison=False)
    poisoned = _make_store(tmp_path, "poisoned.csv", poison=True)
    data_range = (clean.date(0), clean.date(T_IDX))

    shas = []
    for name, store in (("clean", clean), ("poisoned", poisoned)):
        with Registry(tmp_path / f"reg_{name}.db") as reg:
            from quantbot.backtest import prereg, walkforward

            prereg.seal(reg, "sentinel-strat", GRID, data_range,
                        walkforward.folds_spec(SMALL_METH))
            res = judge.evaluate_oos(
                reg, store, "sentinel-strat", GRID, data_range,
                momentum_top1, LOW_COSTS, SMALL_METH, LOOSE_GATES,
            )
            shas.append(res.artifact_sha)

    assert shas[0] == shas[1], (
        "미래 데이터 변조가 판정 결과를 바꿨다 — look-ahead 누출"
    )
