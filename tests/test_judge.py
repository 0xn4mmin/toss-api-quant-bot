"""DoD: OOS 1회 강제 (IMPL-05)·BT-G1 위반 → rejected(INV-04) (BT-G7)."""

from __future__ import annotations

import dataclasses

import pytest

from quantbot.backtest import judge, prereg, walkforward
from quantbot.backtest.data import MarketDataStore
from conftest import (
    LOOSE_GATES,
    LOW_COSTS,
    SMALL_METH,
    buy_and_hold_first,
    crash_path,
    momentum_top1,
    trading_dates,
    write_csv,
)

GRID = {"lookback": [3, 5, 8]}
SID = "judge-strat"


def _range(store):
    return (store.date(0), store.date(len(store) - 1))


def _seal_and_judge(registry, store, sid, grid, signal_fn, gates=LOOSE_GATES):
    rng = _range(store)
    prereg.seal(registry, sid, grid, rng, walkforward.folds_spec(SMALL_METH))
    return judge.evaluate_oos(
        registry, store, sid, grid, rng, signal_fn, LOW_COSTS, SMALL_METH, gates
    )


def test_passing_strategy_transitions_to_paper(registry, uptrend_store):
    """전 게이트 통과 → backtest→paper 전이 (LC-G2)."""
    res = _seal_and_judge(registry, uptrend_store, SID, GRID, momentum_top1)
    assert res.flags == {f"BT-G{i}": True for i in range(1, 7)}, res.metrics
    assert res.transition == judge.STATE_PAPER
    trans = registry.transitions(SID)
    assert len(trans) == 1
    assert (trans[0]["from_state"], trans[0]["to_state"]) == ("backtest", "paper")
    # 아티팩트의 n_configs_tried는 registry가 센 값과 일치
    art = registry.artifacts(strategy_id=SID, kind=judge.ARTIFACT_JUDGEMENT)[0]
    assert art["payload"]["n_configs_tried"] == res.n_configs_tried > 0


def test_second_oos_opening_is_refused(registry, uptrend_store):
    """DoD: OOS 2회 개봉 시도 거부 — 판정 입력(게이트 상수)이 달라지면 재평가 불가."""
    _seal_and_judge(registry, uptrend_store, SID, GRID, momentum_top1)
    looser = dataclasses.replace(LOOSE_GATES, g3_min_sharpe=0.1)
    with pytest.raises(judge.OosAlreadyOpenedError, match="새 전략 id"):
        judge.evaluate_oos(
            registry, uptrend_store, SID, GRID, _range(uptrend_store),
            momentum_top1, LOW_COSTS, SMALL_METH, looser,
        )


def test_identical_rerun_is_reproduction_not_reopening(registry, uptrend_store):
    """동일 해시 재실행은 검산으로 허용 — 결과 일치, 생명주기는 안 움직인다."""
    first = _seal_and_judge(registry, uptrend_store, SID, GRID, momentum_top1)
    second = _seal_and_judge(registry, uptrend_store, SID, GRID, momentum_top1)
    assert second.reproduction and second.reproduction_match is True
    assert second.transition is None
    assert second.flags == first.flags
    assert len(registry.transitions(SID)) == 1  # 전이는 최초 1회뿐
    events = registry.events(judge.EVENT_OOS_REPRODUCED)
    assert len(events) == 1 and events[0]["payload"]["match"] is True


def test_mdd_budget_violation_is_rejected_inv04(registry, tmp_path):
    """DoD: 합성 급락 데이터에서 BT-G1 위반 전략이 rejected(INV-04)로 기록된다."""
    n = 160
    dates = trading_dates("2018-01-02", n)
    closes = crash_path(
        n, seed=3, crash_at=0.60, crash_len=12, crash_daily=-0.04,
        symbols=("AAA", "BBB"),
    )
    store = MarketDataStore.from_csv(write_csv(tmp_path / "crash.csv", dates, closes))
    res = _seal_and_judge(
        registry, store, "crash-strat", {"weight": [1.0]}, buy_and_hold_first
    )
    assert res.flags["BT-G1"] is False, res.metrics["oos_mdd"]
    assert res.metrics["oos_mdd"] > LOOSE_GATES.g1_max_oos_mdd
    assert res.transition == judge.STATE_REJECTED
    trans = registry.transitions("crash-strat")[0]
    assert trans["to_state"] == "rejected"
    assert judge.REASON_INV04 in trans["reason"]


def test_judgement_artifact_is_append_only(registry, uptrend_store):
    """판정 결과 은폐 불가 — 아티팩트 UPDATE는 sqlite가 ABORT (BT-G7)."""
    import sqlite3

    _seal_and_judge(registry, uptrend_store, SID, GRID, momentum_top1)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        registry.connection.execute(
            "UPDATE artifacts SET payload = '{}' WHERE kind = ?",
            (judge.ARTIFACT_JUDGEMENT,),
        )
